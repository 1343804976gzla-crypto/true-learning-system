"""
AI客户端模块 - DeepSeek版本
"""

import os
import json
import asyncio
from typing import Dict
from pathlib import Path
import openai
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

class AIClient:
    """AI客户端 - DeepSeek"""
    
    def __init__(self):
        # DeepSeek配置
        self.api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        self.base_url = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").strip()
        self.model = (os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip()
        
        self.client = (
            openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
            if self.api_key
            else None
        )
        
        print(f"[AIClient] DeepSeek初始化完成")
        print(f"[AIClient] 模型: {self.model} | enabled={bool(self.client)}")
    
    async def generate_content(self, prompt: str, max_tokens: int = 4000,
                               temperature: float = 0.3, timeout: int = 180) -> str:
        if self.client is None:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置，无法调用 DeepSeek")
        messages = [{"role": "user", "content": prompt}]
        
        try:
            def _call():
                return self.client.chat.completions.create(
                    model=self.model, messages=messages,
                    max_tokens=max_tokens, temperature=temperature
                )
            
            loop = asyncio.get_event_loop()
            response = await asyncio.wait_for(loop.run_in_executor(None, _call), timeout=timeout)
            return response.choices[0].message.content
        except asyncio.TimeoutError:
            raise TimeoutError("DeepSeek请求超时")
        except Exception as e:
            print(f"[AIClient] 错误: {e}")
            raise
    
    async def generate_json_safe(self, prompt: str, schema: Dict, max_tokens: int = 4000,
                                  max_retries: int = 3) -> Dict:
        json_prompt = f"{prompt}\n\n请返回JSON格式：\n{json.dumps(schema, indent=2, ensure_ascii=False)}\n只返回JSON："
        
        for attempt in range(max_retries):
            try:
                text = await self.generate_content(json_prompt, max_tokens=max_tokens, temperature=0.2)
                return self._clean_and_parse_json(text)
            except Exception as e:
                print(f"[AIClient] 重试 {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(1)
        
        raise Exception("多次重试失败")
    
    def _clean_and_parse_json(self, text: str) -> Dict:
        text = text.strip()
        if text.startswith("```json"): text = text[7:]
        if text.startswith("```"): text = text[3:]
        if text.endswith("```"): text = text[:-3]
        text = text.strip()
        return json.loads(text)

_ai_client = None

def get_ai_client():
    global _ai_client
    if _ai_client is None:
        _ai_client = AIClient()
    return _ai_client

def reset_ai_client():
    global _ai_client
    _ai_client = None
    print("[AIClient] 已重置")
