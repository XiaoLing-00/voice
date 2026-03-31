def _lc_tool_to_openai(tool_obj) -> dict:
    """把 LangChain @tool 对象转换为 OpenAI tools 格式。"""
    schema = (
        tool_obj.args_schema.schema()
        if tool_obj.args_schema
        else {"properties": {}, "type": "object"}
    )
    return {
        "type": "function",
        "function": {
            "name": tool_obj.name,
            "description": tool_obj.description or "",
            "parameters": {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        },
    }