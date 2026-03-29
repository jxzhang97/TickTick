from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Optional, Union

from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.db.models.task_shadow import TaskShadow
from ticktick_telegram_assistant.domain.schemas import PlannedAction
from ticktick_telegram_assistant.integrations.ticktick_client import (
    TickTickChecklistItem,
    TickTickProject,
    TickTickTask,
    TickTickTaskCreate,
    TickTickTaskPatch,
)
from ticktick_telegram_assistant.repositories.task_shadows import TaskShadowRepository
from ticktick_telegram_assistant.repositories.users import UserRepository
from ticktick_telegram_assistant.services.message_renderer import MessageRenderer


@dataclass
class TaskCommandExecutionCache:
    projects: list[TickTickProject] | None = None
    tasks: list[TickTickTask] | None = None
    moved_project_ids: set[str] = field(default_factory=set)


class TaskCommandService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        ticktick_client,
        message_renderer: Optional[MessageRenderer] = None,
    ) -> None:
        self._session_factory = session_factory
        self._ticktick_client = ticktick_client
        self._message_renderer = message_renderer or MessageRenderer()

    async def execute_action(
        self,
        *,
        telegram_user_id: str,
        action: PlannedAction,
        now: Optional[datetime] = None,
        execution_cache: TaskCommandExecutionCache | None = None,
    ) -> str:
        if action.action_type == "create_task":
            return await self._create_task(
                telegram_user_id=telegram_user_id,
                action=action,
                execution_cache=execution_cache,
            )
        if action.action_type == "complete_task":
            return await self._complete_task(
                telegram_user_id=telegram_user_id,
                action=action,
                execution_cache=execution_cache,
            )
        if action.action_type == "update_task":
            return await self._update_task(
                telegram_user_id=telegram_user_id,
                action=action,
                execution_cache=execution_cache,
            )
        return "这一步我还没稳定接好，所以先不冒险替你写入。"

    async def _create_task(
        self,
        *,
        telegram_user_id: str,
        action: PlannedAction,
        execution_cache: TaskCommandExecutionCache | None = None,
    ) -> str:
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

        projects = await self._load_projects(
            access_token=access_token,
            execution_cache=execution_cache,
        )
        project = self._select_project(projects=projects, requested_name=action.payload.get("list_name"))
        if project is None:
            return "我暂时没找到一个可写入的 TickTick list，所以这条先没有落下去。"

        semantic_type = str(action.payload.get("semantic_type") or "memo")
        due_at = self._coerce_datetime(action.payload.get("due_at"))
        start_at = self._coerce_datetime(action.payload.get("start_at"))
        end_at = self._coerce_datetime(action.payload.get("end_at"))
        duration_minutes = self._coerce_duration_minutes(action.payload.get("duration_minutes"))
        start_at, due_at, end_at = self._resolve_time_span(
            start_at=start_at,
            due_at=due_at,
            end_at=end_at,
            duration_minutes=duration_minutes,
        )
        window_start = self._coerce_datetime(action.payload.get("window_start"))
        window_end = self._coerce_datetime(action.payload.get("window_end"))
        raw_nl_time = self._clean_optional_text(action.payload.get("raw_nl_time"))
        repeat_rule = self._normalize_repeat_rule(
            action.payload.get("repeat_rule") or action.payload.get("repeatFlag")
        )
        priority = self._coerce_priority(action.payload.get("priority"))
        tags = self._normalize_tags(action.payload.get("tags"))
        checklist_items = self._extract_checklist_items(action.payload)
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
            repeatFlag=repeat_rule,
            priority=priority,
            items=checklist_items,
            tags=tags or None,
        )
        created = await self._ticktick_client.create_task(access_token=access_token, task=create_payload)
        action.target_task_id = created.id
        self._remember_created_task(execution_cache=execution_cache, task=created)

        with self._session_factory() as session:
            TaskShadowRepository(session).add(
                TaskShadow(
                    user_id=user_id,
                    ticktick_task_id=created.id,
                    semantic_type=semantic_type,
                    normalized_title=title,
                    list_name=project.name,
                    tags_json=tags or None,
                    due_at=due_at,
                    start_at=start_at,
                    end_at=end_at,
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
            start_at=start_at,
            due_at=due_at,
            raw_nl_time=raw_nl_time,
            notes=self._build_advanced_field_notes(
                repeat_rule=repeat_rule,
                tags=tags,
                checklist_items=checklist_items,
            ),
        )

    def _select_project(
        self,
        *,
        projects: list[TickTickProject],
        requested_name: Optional[object],
    ) -> Optional[TickTickProject]:
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
        description: Optional[str],
        semantic_type: str,
        raw_nl_time: Optional[str],
    ) -> Optional[str]:
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
        start_at: Optional[datetime],
        due_at: Optional[datetime],
        raw_nl_time: Optional[str],
        notes: Optional[list[str]] = None,
    ) -> str:
        if semantic_type == "explicit_time" and start_at is not None and due_at is not None and due_at > start_at:
            base_reply = (
                "好，我已经替你记进 TickTick 了："
                f"{self._message_renderer.render_weekday(start_at)} "
                f"{start_at.strftime('%H:%M')}-{due_at.strftime('%H:%M')} {title}"
            )
        elif semantic_type == "explicit_time" and due_at is not None:
            base_reply = (
                "好，我已经替你记进 TickTick 了："
                f"{self._message_renderer.render_weekday(due_at)} {due_at.strftime('%H:%M')} {title}"
            )
        elif semantic_type == "windowed" and raw_nl_time:
            base_reply = f"好，我先把这条按时间窗口任务记进 TickTick 了：{raw_nl_time} {title}"
        else:
            base_reply = f"好，我已经替你记进 TickTick 了：{title}"
        return self._append_notes(base_reply, notes)

    async def _complete_task(
        self,
        *,
        telegram_user_id: str,
        action: PlannedAction,
        execution_cache: TaskCommandExecutionCache | None = None,
    ) -> str:
        title = self._clean_optional_text(action.payload.get("title"))

        with self._session_factory() as session:
            user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
            if user is None or not user.ticktick_access_token:
                return "我这边还没连上你的 TickTick，所以现在还不能替你勾完成。"
            access_token = user.ticktick_access_token

        tasks = await self._load_tasks(
            access_token=access_token,
            execution_cache=execution_cache,
        )
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
        self._remember_completed_task(execution_cache=execution_cache, task_id=match_or_reply.id)
        return f"好，这条我帮你勾完成了：{match_or_reply.title}"

    async def _update_task(
        self,
        *,
        telegram_user_id: str,
        action: PlannedAction,
        execution_cache: TaskCommandExecutionCache | None = None,
    ) -> str:
        payload = action.payload
        match_title = self._clean_optional_text(payload.get("match_title"))

        with self._session_factory() as session:
            user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
            if user is None or not user.ticktick_access_token:
                return "我这边还没连上你的 TickTick，所以现在还不能替你改任务。"
            user_id = user.id
            access_token = user.ticktick_access_token
            timezone_name = user.current_timezone

        tasks = await self._load_tasks(
            access_token=access_token,
            execution_cache=execution_cache,
        )
        match_or_reply = self._resolve_open_task(
            tasks=tasks,
            requested_title=match_title,
            target_task_id=action.target_task_id,
            missing_message=f"我暂时没找到这条还没完成的任务：{match_title}" if match_title else "我还没抓稳你要改的是哪条任务。",
        )
        if isinstance(match_or_reply, str):
            return match_or_reply

        target_task = match_or_reply
        requested_list_name = self._clean_optional_text(payload.get("list_name"))
        target_project_id = target_task.projectId
        target_list_name: Optional[str] = None
        if requested_list_name:
            projects = await self._load_projects(
                access_token=access_token,
                execution_cache=execution_cache,
            )
            target_project = self._select_project(projects=projects, requested_name=requested_list_name)
            if target_project is None:
                return f"我没找到你说的 list：{requested_list_name}"
            target_project_id = target_project.id
            target_list_name = target_project.name

        with self._session_factory() as session:
            existing_shadow = (
                session.query(TaskShadow)
                .filter(TaskShadow.ticktick_task_id == target_task.id)
                .one_or_none()
            )

        description_present = "description" in payload
        description = self._clean_optional_text(payload.get("description")) if description_present else None
        description_mode = self._clean_optional_text(payload.get("description_mode")) or "replace"
        due_at_present = "due_at" in payload
        due_at = self._coerce_datetime(payload.get("due_at")) if due_at_present else None
        start_at_present = "start_at" in payload
        start_at = self._coerce_datetime(payload.get("start_at")) if start_at_present else None
        end_at_present = "end_at" in payload
        end_at = self._coerce_datetime(payload.get("end_at")) if end_at_present else None
        duration_present = "duration_minutes" in payload
        duration_minutes = self._coerce_duration_minutes(payload.get("duration_minutes")) if duration_present else None
        start_at, due_at, end_at = self._resolve_time_span(
            start_at=start_at,
            due_at=due_at,
            end_at=end_at,
            duration_minutes=duration_minutes,
        )
        semantic_type_present = "semantic_type" in payload
        semantic_type = self._clean_optional_text(payload.get("semantic_type")) if semantic_type_present else None
        window_start_present = "window_start" in payload
        window_start = self._coerce_datetime(payload.get("window_start")) if window_start_present else None
        window_end_present = "window_end" in payload
        window_end = self._coerce_datetime(payload.get("window_end")) if window_end_present else None
        raw_nl_time_present = "raw_nl_time" in payload
        raw_nl_time = self._clean_optional_text(payload.get("raw_nl_time")) if raw_nl_time_present else None
        repeat_rule_present = "repeat_rule" in payload or "repeatFlag" in payload
        repeat_rule = self._resolve_repeat_rule_update(payload) if repeat_rule_present else None
        priority_present = "priority" in payload
        priority = self._coerce_priority(payload.get("priority")) if priority_present else None
        tags_present = "tags" in payload
        tags = self._normalize_tags(payload.get("tags")) if tags_present else []
        checklist_present = any(key in payload for key in ("subtasks", "checklist", "items"))
        checklist_items = self._extract_checklist_items(payload) if checklist_present else []
        new_title_present = "title" in payload
        new_title = self._clean_optional_text(payload.get("title")) if new_title_present else None
        if new_title and match_title and self._normalize_title(new_title) == self._normalize_title(match_title):
            new_title = None

        updated_description = self._merge_description(
            current_description=target_task.desc,
            new_description=description,
            mode=description_mode,
        )

        effective_semantic_type = semantic_type
        if effective_semantic_type is None:
            if due_at is not None or start_at is not None:
                effective_semantic_type = "explicit_time"
            elif window_start is not None or window_end is not None:
                effective_semantic_type = "windowed"
            elif existing_shadow is not None:
                effective_semantic_type = existing_shadow.semantic_type

        time_or_metadata_present = any(
            [
                due_at_present,
                start_at_present,
                end_at_present,
                duration_present,
                semantic_type_present,
                window_start_present,
                window_end_present,
                raw_nl_time_present,
            ]
        )
        if effective_semantic_type == "windowed":
            if raw_nl_time_present:
                window_text = raw_nl_time
            elif window_start_present or window_end_present:
                window_text = None
            elif existing_shadow is not None and existing_shadow.semantic_type == "windowed":
                window_text = existing_shadow.raw_nl_time
            else:
                window_text = None
            base_description = updated_description if updated_description is not None else target_task.desc
            synced_description = self._sync_window_description(base_description, window_text)
            if updated_description is not None:
                if synced_description != updated_description:
                    updated_description = synced_description
            elif synced_description != target_task.desc:
                updated_description = synced_description
        elif time_or_metadata_present:
            base_description = updated_description if updated_description is not None else target_task.desc
            synced_description = self._sync_window_description(base_description, None)
            if updated_description is not None:
                if synced_description != updated_description:
                    updated_description = synced_description
            elif synced_description != target_task.desc:
                updated_description = synced_description

        if (
            not time_or_metadata_present
            and updated_description is None
            and new_title is None
            and target_project_id == target_task.projectId
            and not repeat_rule_present
            and not priority_present
            and not tags_present
            and not checklist_present
        ):
            return "我理解成你要改这条任务，但还没抓稳具体要改什么。"

        patch_kwargs: dict[str, Any] = {
            "id": target_task.id,
            "projectId": target_project_id,
        }
        if new_title is not None:
            patch_kwargs["title"] = new_title
        if updated_description is not None:
            patch_kwargs["desc"] = updated_description
        if due_at is not None:
            patch_kwargs["dueDate"] = self._format_ticktick_datetime(due_at)
        if start_at is not None:
            patch_kwargs["startDate"] = self._format_ticktick_datetime(start_at)
        if due_at is not None or start_at is not None:
            patch_kwargs["timeZone"] = timezone_name
        if repeat_rule_present:
            patch_kwargs["repeatFlag"] = repeat_rule if repeat_rule is not None else ""
        if priority_present and priority is not None:
            patch_kwargs["priority"] = priority
        if checklist_present:
            patch_kwargs["items"] = checklist_items
        if tags_present:
            patch_kwargs["tags"] = tags

        patch = TickTickTaskPatch(**patch_kwargs)
        await self._ticktick_client.update_task(
            access_token=access_token,
            task_id=target_task.id,
            patch=patch,
        )
        action.target_task_id = target_task.id
        self._remember_updated_task(
            execution_cache=execution_cache,
            original_task=target_task,
            patch=patch,
            moved_project_id=target_project_id if target_project_id != target_task.projectId else None,
        )

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
                    semantic_type=effective_semantic_type or ("explicit_time" if due_at else "memo"),
                )
                TaskShadowRepository(session).add(shadow)
            if semantic_type is not None:
                shadow.semantic_type = semantic_type
            elif due_at is not None or start_at is not None:
                shadow.semantic_type = "explicit_time"
            elif window_start is not None or window_end is not None:
                shadow.semantic_type = "windowed"
            elif shadow.semantic_type not in {"explicit_time", "windowed"} and existing_shadow is not None:
                shadow.semantic_type = existing_shadow.semantic_type
            shadow.normalized_title = new_title or target_task.title
            shadow.list_name = target_list_name or shadow.list_name
            if tags_present:
                shadow.tags_json = tags
            elif shadow.tags_json is None and existing_shadow is not None:
                shadow.tags_json = existing_shadow.tags_json
            if shadow.semantic_type == "explicit_time":
                if due_at is not None:
                    shadow.due_at = due_at
                elif existing_shadow is not None and existing_shadow.semantic_type == "explicit_time":
                    shadow.due_at = existing_shadow.due_at
                if start_at is not None:
                    shadow.start_at = start_at
                elif existing_shadow is not None and existing_shadow.semantic_type == "explicit_time":
                    shadow.start_at = existing_shadow.start_at
                if end_at is not None:
                    shadow.end_at = end_at
                elif existing_shadow is not None and existing_shadow.semantic_type == "explicit_time":
                    shadow.end_at = existing_shadow.end_at
                shadow.window_start = None
                shadow.window_end = None
                shadow.raw_nl_time = None
                shadow.timezone_mode = "fixed"
            elif shadow.semantic_type == "windowed":
                shadow.due_at = None
                shadow.start_at = None
                shadow.end_at = None
                if window_start_present:
                    shadow.window_start = window_start
                elif existing_shadow is not None and existing_shadow.semantic_type == "windowed":
                    shadow.window_start = existing_shadow.window_start
                else:
                    shadow.window_start = None
                if window_end_present:
                    shadow.window_end = window_end
                elif existing_shadow is not None and existing_shadow.semantic_type == "windowed":
                    shadow.window_end = existing_shadow.window_end
                else:
                    shadow.window_end = None
                if raw_nl_time_present:
                    shadow.raw_nl_time = raw_nl_time
                elif existing_shadow is not None and existing_shadow.semantic_type == "windowed":
                    shadow.raw_nl_time = existing_shadow.raw_nl_time
                else:
                    shadow.raw_nl_time = None
                shadow.timezone_mode = "floating"
            else:
                if due_at is not None:
                    shadow.due_at = due_at
                elif existing_shadow is not None:
                    shadow.due_at = existing_shadow.due_at
                if start_at is not None:
                    shadow.start_at = start_at
                elif existing_shadow is not None:
                    shadow.start_at = existing_shadow.start_at
                if end_at is not None:
                    shadow.end_at = end_at
                elif existing_shadow is not None:
                    shadow.end_at = existing_shadow.end_at
                if raw_nl_time_present:
                    shadow.raw_nl_time = raw_nl_time
                elif existing_shadow is not None:
                    shadow.raw_nl_time = existing_shadow.raw_nl_time
                if semantic_type is None and existing_shadow is not None:
                    shadow.timezone_mode = existing_shadow.timezone_mode
            shadow.last_synced_at = datetime.now(timezone.utc)
            session.commit()

        return self._render_update_reply(
            title=new_title or target_task.title,
            start_at=start_at,
            due_at=due_at,
            description_changed=updated_description is not None,
            moved_list_name=target_list_name,
            notes=self._build_advanced_field_notes(
                repeat_rule=repeat_rule,
                tags=tags if tags_present else [],
                checklist_items=checklist_items if checklist_present else [],
                repeat_rule_cleared=repeat_rule_present and (repeat_rule == ""),
                tags_cleared=tags_present and not tags,
                checklist_cleared=checklist_present and not checklist_items,
            ),
        )

    async def _load_projects(
        self,
        *,
        access_token: str,
        execution_cache: TaskCommandExecutionCache | None,
    ) -> list[TickTickProject]:
        if execution_cache is not None and execution_cache.projects is not None:
            return execution_cache.projects
        projects = await self._ticktick_client.list_projects(access_token=access_token)
        if execution_cache is not None:
            execution_cache.projects = list(projects)
        return projects

    async def _load_tasks(
        self,
        *,
        access_token: str,
        execution_cache: TaskCommandExecutionCache | None,
    ) -> list[TickTickTask]:
        if execution_cache is not None and execution_cache.tasks is not None:
            return execution_cache.tasks
        tasks = await self._ticktick_client.list_tasks(access_token=access_token, since=None)
        if execution_cache is not None:
            execution_cache.tasks = list(tasks)
        return tasks

    def _remember_created_task(
        self,
        *,
        execution_cache: TaskCommandExecutionCache | None,
        task: TickTickTask,
    ) -> None:
        if execution_cache is None or execution_cache.tasks is None:
            return
        execution_cache.tasks.append(task)

    def _remember_completed_task(
        self,
        *,
        execution_cache: TaskCommandExecutionCache | None,
        task_id: str,
    ) -> None:
        if execution_cache is None or execution_cache.tasks is None:
            return
        for task in execution_cache.tasks:
            if task.id == task_id:
                task.completed = True
                task.status = 2
                return

    def _remember_updated_task(
        self,
        *,
        execution_cache: TaskCommandExecutionCache | None,
        original_task: TickTickTask,
        patch: TickTickTaskPatch,
        moved_project_id: str | None,
    ) -> None:
        if execution_cache is None or execution_cache.tasks is None:
            return
        for task in execution_cache.tasks:
            if task.id != original_task.id:
                continue
            if patch.title is not None:
                task.title = patch.title
            if patch.desc is not None:
                task.desc = patch.desc
            if patch.startDate is not None:
                task.startDate = patch.startDate
            if patch.dueDate is not None:
                task.dueDate = patch.dueDate
            if patch.timeZone is not None:
                task.timeZone = patch.timeZone
            if patch.repeatFlag is not None:
                task.repeatFlag = patch.repeatFlag or None
            if patch.priority is not None:
                task.priority = patch.priority
            if patch.items is not None:
                task.items = list(patch.items)
            if patch.tags is not None:
                task.tags = list(patch.tags)
            if moved_project_id is not None:
                task.projectId = moved_project_id
            return

    def _resolve_open_task(
        self,
        *,
        tasks: list[TickTickTask],
        requested_title: Optional[str],
        target_task_id: Optional[str],
        missing_message: str,
    ) -> Union[TickTickTask, str]:
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

    def _coerce_datetime(self, value: Optional[object]) -> Optional[datetime]:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            return None
        normalized = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)

    def _coerce_priority(self, value: Optional[object]) -> Optional[int]:
        cleaned = self._clean_optional_text(value)
        if cleaned is None:
            return None
        try:
            return int(cleaned)
        except (TypeError, ValueError):
            return None

    def _coerce_duration_minutes(self, value: Optional[object]) -> Optional[int]:
        cleaned = self._clean_optional_text(value)
        if cleaned is None:
            return None
        try:
            minutes = int(cleaned)
        except (TypeError, ValueError):
            return None
        return minutes if minutes > 0 else None

    def _format_ticktick_datetime(self, value: datetime) -> str:
        return value.strftime("%Y-%m-%dT%H:%M:%S%z")

    def _clean_optional_text(self, value: Optional[object]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _normalize_title(self, text: str) -> str:
        return "".join(text.casefold().split())

    def _normalize_tags(self, value: Optional[object]) -> list[str]:
        if value is None:
            return []
        raw_items: list[object]
        if isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        elif isinstance(value, str):
            raw_items = [part.strip() for part in value.split(",")]
        else:
            raw_items = [value]

        tags: list[str] = []
        for item in raw_items:
            text = self._clean_optional_text(item)
            if text and text not in tags:
                tags.append(text)
        return tags

    def _resolve_repeat_rule_update(self, payload: dict[str, Any]) -> Optional[str]:
        raw_value: Optional[object]
        if "repeat_rule" in payload:
            raw_value = payload.get("repeat_rule")
        else:
            raw_value = payload.get("repeatFlag")
        cleaned = self._clean_optional_text(raw_value)
        if cleaned is None:
            return ""
        lowered = cleaned.casefold()
        if lowered in {"none", "null", "no repeat", "not repeat", "no-repeat", "取消循环", "不重复"}:
            return ""
        return self._normalize_repeat_rule(cleaned)

    def _sync_window_description(self, description: str, raw_nl_time: Optional[str]) -> str:
        lines = [line for line in description.splitlines() if not line.startswith("时间窗口：")]
        if raw_nl_time:
            lines.append(f"时间窗口：{raw_nl_time}")
        return "\n".join(lines).strip()

    def _normalize_repeat_rule(self, value: Optional[object]) -> Optional[str]:
        cleaned = self._clean_optional_text(value)
        if cleaned is None:
            return None
        normalized = cleaned.casefold().replace(" ", "")
        if "freq=" in normalized:
            return cleaned
        weekday_match = re.search(r"每周([一二三四五六日天])", normalized)
        if weekday_match:
            weekday = {
                "一": "MO",
                "二": "TU",
                "三": "WE",
                "四": "TH",
                "五": "FR",
                "六": "SA",
                "日": "SU",
                "天": "SU",
            }[weekday_match.group(1)]
            return f"FREQ=WEEKLY;BYDAY={weekday}"
        mapping = {
            "每天": "FREQ=DAILY",
            "每日": "FREQ=DAILY",
            "每周": "FREQ=WEEKLY",
            "每周自动循环": "FREQ=WEEKLY",
            "每月": "FREQ=MONTHLY",
            "每月自动循环": "FREQ=MONTHLY",
            "工作日": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
            "每个工作日": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        }
        return mapping.get(normalized, cleaned)

    def _resolve_time_span(
        self,
        *,
        start_at: Optional[datetime],
        due_at: Optional[datetime],
        end_at: Optional[datetime],
        duration_minutes: Optional[int],
    ) -> tuple[Optional[datetime], Optional[datetime], Optional[datetime]]:
        resolved_end_at = end_at
        if resolved_end_at is None and start_at is not None and duration_minutes is not None:
            resolved_end_at = start_at + timedelta(minutes=duration_minutes)
        resolved_due_at = due_at
        if resolved_due_at is None and resolved_end_at is not None:
            resolved_due_at = resolved_end_at
        return start_at, resolved_due_at, resolved_end_at

    def _resolve_semantic_type(
        self,
        *,
        explicit_value: Optional[str],
        start_at: Optional[datetime],
        due_at: Optional[datetime],
        window_start: Optional[datetime],
        window_end: Optional[datetime],
        fallback: Optional[str],
    ) -> Optional[str]:
        if explicit_value:
            return explicit_value
        if start_at is not None or due_at is not None:
            return "explicit_time"
        if window_start is not None or window_end is not None:
            return "windowed"
        return fallback

    def _extract_checklist_items(self, payload: dict[str, Any]) -> list[TickTickChecklistItem]:
        raw_items = payload.get("subtasks")
        if raw_items is None:
            raw_items = payload.get("checklist")
        if raw_items is None:
            raw_items = payload.get("items")
        if raw_items is None:
            return []
        if isinstance(raw_items, (str, bytes)):
            candidates = [raw_items]
        elif isinstance(raw_items, list):
            candidates = raw_items
        else:
            return []

        checklist_items: list[TickTickChecklistItem] = []
        for candidate in candidates:
            if isinstance(candidate, TickTickChecklistItem):
                checklist_items.append(candidate)
                continue
            if isinstance(candidate, dict):
                title = self._clean_optional_text(candidate.get("title"))
                if not title:
                    continue
                item_kwargs: dict[str, Any] = {"title": title}
                for key in ("id", "status", "completedTime", "isAllDay", "sortOrder", "startDate", "timeZone"):
                    if key in candidate and candidate[key] is not None:
                        item_kwargs[key] = candidate[key]
                checklist_items.append(TickTickChecklistItem(**item_kwargs))
                continue
            title = self._clean_optional_text(candidate)
            if title:
                checklist_items.append(TickTickChecklistItem(title=title))
        return checklist_items

    def _merge_description(
        self,
        *,
        current_description: str,
        new_description: Optional[str],
        mode: str,
    ) -> Optional[str]:
        if new_description is None:
            return None
        if mode == "append" and current_description.strip():
            return f"{current_description.rstrip()}\n{new_description}"
        return new_description

    def _build_advanced_field_notes(
        self,
        *,
        repeat_rule: Optional[str],
        tags: list[str],
        checklist_items: list[TickTickChecklistItem],
        repeat_rule_cleared: bool = False,
        tags_cleared: bool = False,
        checklist_cleared: bool = False,
    ) -> list[str]:
        notes: list[str] = []
        if repeat_rule_cleared:
            notes.append("重复规则已清除")
        elif repeat_rule:
            notes.append(f"重复规则已设置为 {repeat_rule}")
        if tags_cleared:
            notes.append("标签已清空")
        elif tags:
            notes.append("已尝试同步标签，若 TickTick 侧未显示则说明该字段当前接口不稳定")
        if checklist_cleared:
            notes.append("清单已清空")
        elif checklist_items:
            notes.append(f"已附带 {len(checklist_items)} 条清单")
        return notes

    def _append_notes(self, message: str, notes: Optional[list[str]]) -> str:
        if not notes:
            return message
        return f"{message}；{'；'.join(notes)}"

    def _render_update_reply(
        self,
        *,
        title: str,
        start_at: Optional[datetime],
        due_at: Optional[datetime],
        description_changed: bool,
        moved_list_name: Optional[str],
        notes: Optional[list[str]] = None,
    ) -> str:
        parts: list[str] = []
        if start_at is not None and due_at is not None and due_at > start_at:
            parts.append(
                f"{self._message_renderer.render_weekday(start_at)} {start_at.strftime('%H:%M')}-{due_at.strftime('%H:%M')}"
            )
        elif due_at is not None:
            parts.append(f"{self._message_renderer.render_weekday(due_at)} {due_at.strftime('%H:%M')}")
        parts.append(title)
        detail_bits: list[str] = []
        if description_changed:
            detail_bits.append("说明已更新")
        if moved_list_name:
            detail_bits.append(f"已移到 {moved_list_name}")
        detail = f"，{'，'.join(detail_bits)}" if detail_bits else ""
        return self._append_notes(f"好，我已经替你改好了：{' '.join(parts)}{detail}", notes)
