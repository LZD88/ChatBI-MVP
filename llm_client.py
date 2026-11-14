"""
LLM 客户端模块

负责调用大模型 API 生成 SQL。
将 LLM 调用封装为独立模块，便于后续切换不同模型或增加重试逻辑。

第11课增强：新增 generate_sql_stream 流式方法，逐 chunk 产出 SQL 文本，
为 SSE 推送提供底层能力。保留原有 generate_sql 不动，确保向后兼容。
"""

import re
from typing import Any, Generator

import sqlglot
from openai import APIConnectionError, APITimeoutError, OpenAI, OpenAIError

from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.settings import Settings, get_settings
from chatbi.application.progress import checkpoint, remaining_timeout


class LLMClient:
    """LLM API 客户端"""

    def __init__(self, settings: Settings | None = None, client: Any | None = None):
        self.settings = settings or get_settings()
        if client is None:
            if self.settings.openai_api_key is None:
                raise ValueError("OPENAI_API_KEY is required to create LLMClient")
            client = OpenAI(
                api_key=self.settings.openai_api_key.get_secret_value(),
                base_url=self.settings.openai_base_url,
                timeout=self.settings.llm_timeout_seconds,
                max_retries=0,
            )
        self.client = client
        self.model = self.settings.llm_model
        self.embedding_model = self.settings.embedding_model
        self.temperature = self.settings.llm_temperature
        self.max_tokens = self.settings.llm_max_tokens
        self.usage = {"calls": 0, "reported_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def generate_sql(self, system_msg: str, prompt: str) -> str:
        """
        调用 LLM 生成 SQL（同步，一次性返回）

        Args:
            system_msg: 系统角色消息
            prompt: 完整的用户 Prompt

        Returns:
            提取后的纯 SQL 字符串
        """
        raw_output = self.generate_text(system_msg=system_msg, prompt=prompt)
        return self.extract_sql(raw_output)

    @staticmethod
    def extract_sql(raw_output: str | None) -> str:
        if not raw_output or not raw_output.strip():
            raise ChatBIError(ErrorCode.MODEL_OUTPUT_INVALID)
        fenced = re.search(r"```(?:sql)?\s*(.*?)```", raw_output, re.IGNORECASE | re.DOTALL)
        candidate = fenced.group(1).strip() if fenced else raw_output.strip()
        start = re.search(r"\b(?:WITH|SELECT)\b", candidate, re.IGNORECASE)
        if not start:
            raise ChatBIError(ErrorCode.MODEL_OUTPUT_INVALID)
        candidate = candidate[start.start():].strip()
        try:
            statements = sqlglot.parse(candidate, read="mysql")
        except Exception as exc:
            raise ChatBIError(ErrorCode.MODEL_OUTPUT_INVALID) from exc
        if len(statements) != 1 or statements[0] is None:
            raise ChatBIError(ErrorCode.MODEL_OUTPUT_INVALID)
        return statements[0].sql(dialect="mysql")

    def generate_text(self, system_msg: str, prompt: str) -> str:
        """
        调用 LLM 生成普通文本（同步，一次性返回）

        Args:
            system_msg: 系统角色消息
            prompt: 完整的用户 Prompt

        Returns:
            模型返回的原始文本内容
        """
        try:
            checkpoint()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=remaining_timeout(self.settings.llm_timeout_seconds),
            )
            checkpoint()
            self.usage["calls"] += 1
            usage = getattr(response, "usage", None)
            if usage is not None:
                self.usage["reported_calls"] += 1
                self.usage["prompt_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
                self.usage["completion_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
            content = response.choices[0].message.content
            return content.strip() if content else ""
        except (APITimeoutError, APIConnectionError, OpenAIError) as exc:
            raise ChatBIError(ErrorCode.MODEL_UNAVAILABLE) from exc

    def generate_sql_stream(self, system_msg: str, prompt: str) -> Generator[str, None, None]:
        """
        调用 LLM 流式生成 SQL（逐 chunk 产出）

        每次_yield_一段增量文本，调用方可以实时推送给前端。
        流式输出结束后，调用方需要自行拼接完整 SQL 并做 markdown 代码块清理。

        Args:
            system_msg: 系统角色消息
            prompt: 完整的用户 Prompt

        Yields:
            增量文本片段（可能包含 markdown 代码块标记，需在完整拼接后统一清理）
        """
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt}
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
        )

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            delta_content = delta.content if delta and delta.content is not None else None
            if delta_content is not None:
                yield delta_content

    def get_embedding(self, text: str) -> list[float]:
        """
        调用 Embedding API 将文本转为向量

        Args:
            text: 需要向量化的文本

        Returns:
            浮点数列表，维度取决于所用模型（如 text-embedding-3-small 为 1536 维）
        """
        resp = self.client.embeddings.create(
            model=self.embedding_model,
            input=text,
        )
        # OpenAI 兼容接口返回按输入顺序的列表；这里只传了 1 条文本
        embedding = resp.data[0].embedding
        return embedding


if __name__ == '__main__':
    llm = LLMClient()
    vec = llm.get_embedding(text="你好")
    print(f"向量维度: {len(vec)}")
    print(f"前 5 个值: {vec[:5]}")
