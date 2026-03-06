"""加载子 Agent 总结 Prompt 配置（从 YAML 文件）。"""

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

_PROMPTS_CACHE: dict[str, Any] | None = None


def _get_prompts_path() -> Path:
    """获取 prompts YAML 文件路径。"""
    return Path(__file__).parent / "subagent_summary_prompts.yaml"


def load_subagent_summary_prompts() -> dict[str, Any]:
    """
    加载子 Agent 总结 prompt 配置。
    首次加载后缓存，避免重复读取文件。
    """
    global _PROMPTS_CACHE
    if _PROMPTS_CACHE is not None:
        return _PROMPTS_CACHE

    path = _get_prompts_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            logger.warning(f"[SubagentSummary] Invalid prompts YAML structure: {path}")
            _PROMPTS_CACHE = _get_default_prompts()
        else:
            _PROMPTS_CACHE = data
    except FileNotFoundError:
        logger.warning(f"[SubagentSummary] Prompts file not found: {path}, using defaults")
        _PROMPTS_CACHE = _get_default_prompts()
    except yaml.YAMLError as e:
        logger.warning(f"[SubagentSummary] Failed to parse prompts YAML: {e}, using defaults")
        _PROMPTS_CACHE = _get_default_prompts()
    except Exception as e:
        logger.warning(f"[SubagentSummary] Error loading prompts: {e}, using defaults")
        _PROMPTS_CACHE = _get_default_prompts()

    return _PROMPTS_CACHE


def _get_default_prompts() -> dict[str, Any]:
    """内置默认 prompt（当 YAML 加载失败时使用）。"""
    return {
        "single_task": {
            "system": """你是任务结果总结助手，负责将子任务的执行结果自然地呈现给用户。

核心原则：
1. **保持用户原始意图**：根据「任务描述」或「用户原始提问」中用户的具体要求来组织回复
2. **结果导向**：用户要求做什么，就呈现什么（要代码给代码，要描述给描述）
3. **隐匿系统执行过程**：不要提 subagent、task_id 等技术细节
4. **语言一致性（必须遵守）**：根据用户原始提问或任务描述判断输出语言：
   - 若用户用中文提问，必须用中文回复
   - 若用户用英文提问，必须用英文回复
   - 若两者混合，以用户主要使用的语言为准
   - 优先参考「用户原始提问」的语言，若无则参考「任务描述」

请基于以下内容，给出符合用户原始意图的回复：""",
        },
        "batch": {
            "system": """你是任务结果总结助手，负责将多个子任务的执行结果自然地呈现给用户。

核心原则：
1. **保持用户原始意图**：根据每个任务的「任务描述」或「用户原始提问」来组织回复
2. **结果导向**：用户要求做什么，就呈现什么（要代码给代码，要描述给描述）
3. **简洁口语化**：不要提 subagent、task_id 等技术细节
4. **语言一致性（必须遵守）**：根据用户原始提问或任务描述判断输出语言：
   - 若用户用中文提问，必须用中文回复
   - 若用户用英文提问，必须用英文回复
   - 若两者混合，以用户主要使用的语言为准
   - 优先参考「用户原始提问」的语言，若无则参考「任务描述」

输出结构：
1. 总体结论：1-2 句概括所有任务完成情况
2. 分任务要点：按任务逐一列出关键结论（每项 1-2 句）
3. 建议下一步：如有未完成或需用户决策的内容，简要说明""",
            "user_intro": """以下是多个子任务的执行结果，请基于用户原始意图进行回复。

每个任务的「任务描述」告诉用户想要什么，根据它来组织回复。
""",
        },
    }


def get_single_task_system_prompt() -> str:
    """获取单任务总结的 system prompt。"""
    prompts = load_subagent_summary_prompts()
    single = prompts.get("single_task") or {}
    return (single.get("system") or "").strip() or _get_default_prompts()["single_task"]["system"]


def get_batch_system_prompt() -> str:
    """获取批量任务总结的 system prompt。"""
    prompts = load_subagent_summary_prompts()
    batch = prompts.get("batch") or {}
    return (batch.get("system") or "").strip() or _get_default_prompts()["batch"]["system"]


def get_batch_user_intro() -> str:
    """获取批量任务总结的 user prompt 引言部分。"""
    prompts = load_subagent_summary_prompts()
    batch = prompts.get("batch") or {}
    return (batch.get("user_intro") or "").strip() or _get_default_prompts()["batch"]["user_intro"]
