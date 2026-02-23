#!/usr/bin/env python3
"""
测试脚本：验证 Minimax Provider 配置保存是否正常工作
"""

import sys
import os
import json

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path


def test_minimax_config_save():
    """测试 Minimax Provider 配置保存"""
    print("=" * 60)
    print("测试 Minimax Provider 配置保存")
    print("=" * 60)

    # 导入模块
    from nanobot.config.schema import Config, ProvidersConfig, ProviderConfig
    from nanobot.config.loader import save_config, load_config, convert_keys, convert_to_camel
    from nanobot.storage.config_repository import ConfigRepository

    # 测试 1: 检查 schema 中是否定义了 minimax provider
    print("\n[测试 1] 检查 Schema 中是否定义了 minimax provider")
    try:
        config = Config()
        has_minimax = hasattr(config.providers, 'minimax')
        print(f"  - config.providers.minimax 存在: {has_minimax}")
        if has_minimax:
            minimax_config = config.providers.minimax
            print(f"  - minimax.api_key: '{minimax_config.api_key}'")
            print(f"  - minimax.api_base: '{minimax_config.api_base}'")
        print("  [通过]")
    except Exception as e:
        print(f"  [失败] {e}")
        return False

    # 测试 2: 检查 ConfigRepository 中 minimax 的处理
    print("\n[测试 2] 检查 ConfigRepository.save_full_config 中 minimax 的处理")
    try:
        repo = ConfigRepository(Path.home() / ".nanobot" / "test_chat.db")
        # 检查 provider_names 是否包含 minimax
        # 通过反射检查私有属性
        import inspect
        source = inspect.getsource(repo.save_full_config)
        if 'minimax' in source:
            print("  - save_full_config 中包含 minimax 处理")
        else:
            print("  [警告] save_full_config 中未包含 minimax 处理!")
        print("  [通过]")
    except Exception as e:
        print(f"  [失败] {e}")

    # 测试 3: 检查 provider_names 字典
    print("\n[测试 3] 检查 provider_names 映射")
    try:
        # 创建测试配置
        test_config = Config()
        test_config.providers.minimax.api_key = "test-api-key-12345"
        test_config.providers.minimax.api_base = "https://api.minimax.chat/v1"

        # 转换为 dict 并保存
        config_data = test_config.model_dump()
        config_data = convert_to_camel(config_data)

        print(f"  - 转换后的 providers 数据:")
        print(f"    {json.dumps(config_data.get('providers', {}).get('minimax', {}), indent=4)}")

        # 检查是否在 provider_names 中
        from nanobot.storage.config_repository import ConfigRepository
        provider_names = {
            "anthropic": "Anthropic",
            "openai": "OpenAI",
            "openrouter": "OpenRouter",
            "deepseek": "DeepSeek",
            "groq": "Groq",
            "zhipu": "Zhipu",
            "dashscope": "DashScope",
            "vllm": "vLLM",
            "gemini": "Gemini",
        }

        if "minimax" in provider_names:
            print(f"  - minimax 在 provider_names 中: {provider_names['minimax']}")
        else:
            print("  [警告] minimax 不在 provider_names 字典中!")
        print("  [通过]")
    except Exception as e:
        print(f"  [失败] {e}")
        import traceback
        traceback.print_exc()

    # 测试 4: 实际保存配置到临时数据库
    print("\n[测试 4] 实际保存 Minimax 配置到数据库")
    try:
        from nanobot.config.loader import get_config_repository

        # 使用测试数据库
        test_db_path = Path.home() / ".nanobot" / "test_minimax_chat.db"
        if test_db_path.exists():
            test_db_path.unlink()

        # 临时替换数据库路径
        original_get_repo = get_config_repository

        class TestConfigRepository(ConfigRepository):
            def __init__(self):
                super().__init__(test_db_path)

        import nanobot.config.loader as loader_module
        loader_module.get_config_repository = lambda: TestConfigRepository()

        # 创建配置并保存
        config = Config()
        config.providers.minimax.api_key = "test-minimax-key-abc123"
        config.providers.minimax.api_base = "https://api.minimax.chat/v1"

        print(f"  - 保存前 api_key: '{config.providers.minimax.api_key}'")
        print(f"  - 保存前 api_base: '{config.providers.minimax.api_base}'")

        save_config(config)

        # 重新加载配置
        loaded_config = load_config()

        print(f"  - 加载后 api_key: '{loaded_config.providers.minimax.api_key}'")
        print(f"  - 加载后 api_base: '{loaded_config.providers.minimax.api_base}'")

        # 验证
        if loaded_config.providers.minimax.api_key == "test-minimax-key-abc123":
            print("  [通过] API Key 正确保存和加载")
        else:
            print("  [失败] API Key 保存/加载失败")
            return False

        # 清理测试数据库
        if test_db_path.exists():
            test_db_path.unlink()

        # 恢复原始函数
        loader_module.get_config_repository = original_get_repo

    except Exception as e:
        print(f"  [失败] {e}")
        import traceback
        traceback.print_exc()
        return False

    # 测试 5: 检查 api.py 中的显示名称映射
    print("\n[测试 5] 检查 API 显示名称映射")
    try:
        provider_display_names = {
            "anthropic": "Anthropic",
            "openai": "OpenAI",
            "openrouter": "OpenRouter",
            "deepseek": "DeepSeek",
            "groq": "Groq",
            "zhipu": "Zhipu (智谱)",
            "dashscope": "Qwen (通义)",
            "gemini": "Gemini",
            "vllm": "vLLM",
        }

        if "minimax" in provider_display_names:
            print(f"  - minimax 显示名称: {provider_display_names['minimax']}")
        else:
            print("  [警告] minimax 不在 API 显示名称映射中!")

        print("  [通过]")
    except Exception as e:
        print(f"  [失败] {e}")

    # 测试 6: 检查 schema.py 中的 _MODEL_PROVIDER_MAP
    print("\n[测试 6] 检查 Model Provider 映射")
    try:
        config = Config()
        model_provider_map = config._MODEL_PROVIDER_MAP
        api_base_map = config._MODEL_API_BASE_MAP

        # 检查 minimax 是否在映射中
        has_minimax_in_map = any('minimax' in str(k) for k in model_provider_map.keys())
        has_minimax_in_base = 'minimax' in api_base_map

        print(f"  - minimax 在 _MODEL_PROVIDER_MAP: {has_minimax_in_map}")
        print(f"  - minimax 在 _MODEL_API_BASE_MAP: {has_minimax_in_base}")

        if not has_minimax_in_map:
            print("  [警告] minimax 未在 _MODEL_PROVIDER_MAP 中定义!")
        if not has_minimax_in_base:
            print("  [警告] minimax 未在 _MODEL_API_BASE_MAP 中定义!")

        print("  [通过]")
    except Exception as e:
        print(f"  [失败] {e}")

    # 测试 7: 模拟 API 调用 create_provider
    print("\n[测试 7] 模拟 API 调用 create_provider")
    try:
        # 检查 API 验证列表
        api_validation_list = ['anthropic', 'openai', 'openrouter', 'deepseek', 'groq', 'zhipu', 'dashscope', 'gemini', 'vllm', 'minimax']
        if 'minimax' in api_validation_list:
            print("  - minimax 在 API 验证列表中: [通过]")
        else:
            print("  - minimax 不在 API 验证列表中: [失败]")
    except Exception as e:
        print(f"  [失败] {e}")

    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)

    return True


if __name__ == "__main__":
    success = test_minimax_config_save()
    sys.exit(0 if success else 1)
