from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.models.task_shadow import TaskShadow
from ticktick_telegram_assistant.domain.schemas import PlannedAction
from ticktick_telegram_assistant.integrations.ticktick_client import (
    TickTickProject,
    TickTickTask,
    TickTickTaskCreate,
    TickTickTaskPatch,
)
from ticktick_telegram_assistant.repositories.task_shadows import TaskShadowRepository
from ticktick_telegram_assistant.repositories.users import UserRepository
from ticktick_telegram_assistant.services.message_renderer import MessageRenderer


class TaskCommandService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        ticktick_client,
        message_renderer: MessageRenderer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._ticktick_client = ticktick_client
        self._message_renderer = message_renderer or MessageRenderer()

    async def execute_action(
        self,
        *,
        telegram_user_id: str,
        action: PlannedAction,
        now: datetime | None = None,
    ) -> str:
        if action.action_type == "create_task":
            return await self._create_task(telegram_user_id=telegram_user_id, action=action)
        if action.action_type == "complete_task":
            return await self._complete_task(telegram_user_id=telegram_user_id, action=action)
        if action.action_type == "update_task":
            return await self._update_task(telegram_user_id=telegram_user_id, action=action)
        return "这一步我还没稳定接好，所以先不冒险替你写入。"

    async def _create_task(self, *, telegram_user_id: str, action: PlannedAction) -> str:
        title = str(action.payload.get("title", "")).strip()
        if not title:
            return "这条我还没抓稳标题，所以先不往 TickTick 里写，免得记歪。"

        with self._session_factory() as session:
            user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
            if user is None or not user.ticktick_access_token:
                return "我这边还没连上你的 TickTick，所以现在还不能替你创建任务。"
            user_id = user.id
            access_token = user.ticktick_access_token
            timezone_name = user.current_timezone

        projects = await self._ticktick_client.list_projects(access_token=access_token)
        project = self._select_project(projects=projects, requested_name=action.payload.get("list_name"))
        if project is None:
            return "我暂时没找到一个可写入的 TickTick list，所以这条先没有落下去。"

        semantic_type = str(action.payload.get("semantic_type") or "memo")
        due_at = self._coerce_datetime(action.payload.get("due_at"))
        start_at = self._coerce_datetime(action.payload.get("start_at"))
        window_start = self._coerce_datetime(action.payload.get("window_start"))
        window_end = self._coerce_datetime(action.payload.get("window_end"))
        raw_nl_time = self._clean_optional_text(action.payload.get("raw_nl_time"))
        description = self._build_description(
            description=self._clean_optional_text(action.payload.get("description")),
            semantic_type=semantic_type,
            raw_nl_time=raw_nl_time,
        )

        create_payload = TickTickTaskCreate(
            title=title,
            projectId=project.id,
            desc=description or "",
            dueDate=self._format_ticktick_datetime(due_at) if due_at is not None else None,
            startDate=self._format_ticktick_datetime(start_at) if start_at is not None else None,
            timeZone=timezone_name if (due_at or start_at) else None,
        )
        created = await self._ticktick_client.create_task(access_token=access_token, task=create_payload)
        action.target_task_id = created.id

        with self._session_factory() as session:
            TaskShadowRepository(session).add(
                TaskShadow(
                    user_id=user_id,
                    ticktick_task_id=created.id,
                    semantic_type=semantic_type,
                    normalized_title=title,
                    list_name=project.name,
                    due_at=due_at,
                    start_at=start_at,
                    window_start=window_start,
                    window_end=window_end,
                    raw_nl_time=raw_nl_time,
                    timezone_mode="floating" if semantic_type in {"windowed", "memo"} else "fixed",
                    last_synced_at=datetime.now(timezone.utc),
                )
            )
            session.commit()

        return self._render_create_reply(
            title=title,
            semantic_type=semantic_type,
            due_at=due_at,
            raw_nl_time=raw_nl_time,
        )

    def _select_project(
        self,
        *,
        projects: list[TickTickProject],
        requested_name: object | None,
    ) -> TickTickProject | None:
        cleaned_requested_name = self._clean_optional_text(requested_name)
        if cleaned_requested_name:
            lowered = cleaned_requested_name.casefold()
            for project in projects:
                if project.name.casefold() == lowered:
                    return project

        preferred_names = {"telegram inbox", "inbox", "收件箱", "收集箱"}
        for project in projects:
            if project.name.casefold() in preferred_names:
                return project

        for project in projects:
            if project.kind == "TASK":
                return project
        return projects[0] if projects else None

    def _build_description(
        self,
        *,
        description: str | None,
        semantic_type: str,
        raw_nl_time: str | None,
    ) -> str | None:
        lines: list[str] = []
        if description:
            lines.append(description)
        if semantic_type == "windowed" and raw_nl_time:
            lines.append(f"时间窗口：{raw_nl_time}")
        return "\n".join(lines) if lines else None

    def _render_create_reply(
        self,
        *,
        title: str,
        semantic_type: str,
        due_at: datetime | None,
        raw_nl_time: str | None,
    ) -> str:
        if semantic_type == "explicit_time" and due_at is not None:
            return (
                "好，我已经替你记进 TickTick 了："
                f"{self._message_renderer.render_weekday(due_at)} {due_at.strftime('%H:%M')} {title}"
            )
        if semantic_type == "windowed" and raw_nl_time:
            return f"好，我先把这条按时间窗口任务记进 TickTick 了：{raw_nl_time} {title}"
        return f"好，我已经替你记进 TickTick 了：{title}"

    async def _complete_task(self, *, telegram_user_id: str, action: PlannedAction) -> str:
        title = self._clean_optional_text(action.payload.get("title"))

        with self._session_factory() as session:
            user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
            if user is None or not user.ticktick_access_token:
                return "我这边还没连上你的 TickTick，所以现在还不能替你勾完成。"
            access_token = user.ticktick_access_token

        tasks = await self._ticktick_client.list_tasks(access_token=access_token, since=None)
        match_or_reply = self._resolve_open_task(
            tasks=tasks,
            requested_title=title,
            target_task_id=action.target_task_id,
            missing_message=f"我暂时没找到这条还没完成的任务：{title}" if title else "我还没抓稳你要完成的是哪条任务，所以先不冒险替你勾掉。",
        )
        if isinstance(match_or_reply, str):
            return match_or_reply

        await self._ticktick_client.complete_task(
            access_token=access_token,
            project_id=match_or_reply.projectId,
            task_id=match_or_reply.id,
        )
        action.target_task_id = match_or_reply.id
        return f"好，这条我帮你勾完成了：{match_or_reply.title}"

    async def _update_task(self, *, telegram_user_id: str, action: PlannedAction) -> str:
        match_title = self._clean_optional_text(action.payload.get("match_title"))

        with self._session_factory() as session:
            user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
            if user is None or not user.ticktick_access_token:
                return "我这边还没连上你的 TickTick，所以现在还不能替你改任务。"
            user_id = user.id
            access_token = user.ticktick_access_token
            timezone_name = user.current_timezone

        tasks = await self._ticktick_client.list_tasks(access_token=access_token, since=None)
        match_or_reply = self._resolve_open_task(
            tasks=tasks,
            requested_title=match_title,
            target_task_id=action.target_task_id,
            missing_message=f"我暂时没找到这条还没完成的任务：{match_title}" if match_title else "我还没抓稳你要改的是哪条任务。",
        )
        if isinstance(match_or_reply, str):
            return match_or_reply

        target_task = match_or_reply
        requested_list_name = self._clean_optional_text(action.payload.get("list_name"))
        target_project_id = target_task.projectId
        target_list_name: str | None = None
        if requested_list_name:
            projects = await self._ticktick_client.list_projects(access_token=access_token)
            target_project = self._select_project(projects=projects, requested_name=requested_list_name)
            if target_project is None:
                return f"我没找到你说的 list：{requested_list_name}"
            target_project_id = target_project.id
            target_list_name = target_project.name

        description = self._clean_optional_text(action.payload.get("description"))
        description_mode = self._clean_optional_text(action.payload.get("description_mode")) or "replace"
        due_at = self._coerce_datetime(action.payload.get("due_at"))
        start_at = self._coerce_datetime(action.payload.get("start_at"))
        raw_nl_time = self._clean_optional_text(action.payload.get("raw_nl_time"))
        new_title = self._clean_optional_text(action.payload.get("title"))
        if new_title and match_title and self._normalize_title(new_title) == self._normalize_title(match_title):
            new_title = None

        updated_description = self._merge_description(
            current_description=target_task.desc,
            new_description=description,
            mode=description_mode,
        )

        if (
            due_at is None
            and start_at is None
            and updated_description is None
            and new_title is None
            and target_project_id == target_task.projectId
        ):
            return "我理解成你要改这条任务，但还没抓稳具体要改什么。"

        patch = TickTickTaskPatch(
            id=target_task.id,
            projectId=target_project_id,
            title=new_title,
            desc=updated_description,
            dueDate=self._format_ticktick_datetime(due_at) if due_at is not None else None,
            startDate=self._format_ticktick_datetime(start_at) if start_at is not None else None,
            timeZone=timezone_name if (due_at or start_at) else None,
        )
        await self._ticktick_client.update_task(
            access_token=access_token,
            task_id=target_task.id,
            patch=patch,
        )
        action.target_task_id = target_task.id

        with self._session_factory() as session:
            shadow = (
                session.query(TaskShadow)
                .filter(TaskShadow.ticktick_task_id == target_task.id)
                .one_or_none()
            )
            if shadow is None:
                shadow = TaskShadow(
                    user_id=user_id,
                    ticktick_task_id=target_task.id,
                    semantic_type="explicit_time" if due_at else "memo",
                )
                TaskShadowRepository(session).add(shadow)
            shadow.normalized_title = new_title or target_task.title
            shadow.list_name = target_list_name or shadow.list_name
            shadow.due_at = due_at or shadow.due_at
            shadow.start_at = start_at or shadow.start_at
            shadow.raw_nl_time = raw_nl_time or shadow.raw_nl_time
            shadow.timezone_mode = "fixed" if (due_at or start_at) else shadow.timezone_mode
            shadow.last_synced_at = datetime.now(timezone.utc)
            session.commit()

        return self._render_update_reply(
            title=new_title or target_task.title,
            due_at=due_at,
            description_changed=updated_description is not None,
            moved_list_name=target_list_name,
        )

    def _resolve_open_task(
        self,
        *,
        tasks: list[TickTickTask],
        requested_title: str | None,
        target_task_id: str | None,
        missing_message: str,
    ) -> TickTickTask | str:
        open_tasks = [task for task in tasks if not task.completed and (task.status is None or task.status == 0)]
        if target_task_id:
            for task in open_tasks:
                if task.id == target_task_id:
                    return task
            return "我没找到你刚才指的那条未完成任务，所以先不乱动。"

        if not requested_title:
            return "我还没抓稳你具体指的是哪条任务，所以先不冒险替你改。"

        normalized_title = self._normalize_title(requested_title)
        exact_matches = [task for task in open_tasks if self._normalize_title(task.title) == normalized_title]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            return f"我找到不止一条叫“{requested_title}”的未完成任务。你是指哪一条？"

        fuzzy_matches = [
            task
            for task in open_tasks
            if normalized_title in self._normalize_title(task.title)
            or self._normalize_title(task.title) in normalized_title
        ]
        if len(fuzzy_matches) == 1:
            return fuzzy_matches[0]
        if len(fuzzy_matches) > 1:
            return f"我找到几条和“{requested_title}”很像的未完成任务。你是指哪一条？"
        return missing_message

    def _coerce_datetime(self, value: object | None) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            return None
        normalized = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)

    def _format_ticktick_datetime(self, value: datetime) -> str:
        return value.strftime("%Y-%m-%dT%H:%M:%S%z")

    def _clean_optional_text(self, value: object | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _normalize_title(self, text: str) -> str:
        return "".join(text.casefold().split())

    def _merge_description(
        self,
        *,
        current_description: str,
        new_description: str | None,
        mode: str,
    ) -> str | None:
        if new_description is None:
            return None
        if mode == "append" and current_description.strip():
            return f"{current_description.rstrip()}\n{new_description}"
        return new_description

    def _render_update_reply(
        self,
        *,
        title: str,
        due_at: datetime | None,
        description_changed: bool,
        moved_list_name: str | None,
    ) -> str:
        parts: list[str] = []
        if due_at is not None:
            parts.append(f"{self._message_renderer.render_weekday(due_at)} {due_at.strftime('%H:%M')}")
        parts.append(title)
        detail_bits: list[str] = []
        if description_changed:
            detail_bits.append("说明已更新")
        if moved_list_name:
            detail_bits.append(f"已移到 {moved_list_name}")
        detail = f"，{'，'.join(detail_bits)}" if detail_bits else ""
        return f"好，我已经替你改好了：{' '.join(parts)}{detail}"
