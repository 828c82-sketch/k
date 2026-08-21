import os
import json
import asyncio
import aiohttp
from aiohttp import web

chat_history = []
current_target = "호"

slots = {
    "호": {"provider": "groq", "apiKey": "", "model": "llama-3.3-70b-versatile", "sysPrompt": "", "maxTokens": 4096},
    "탱탱": {"provider": "groq", "apiKey": "", "model": "qwen/qwen3.6-27b", "sysPrompt": "", "maxTokens": 4096}
}

# 1. 명령어 처리
def handle_command(text):
    global slots, current_target, chat_history
    
    if text.startswith("***"):
        raw_text = text.replace("***", "").strip()
        tokens = raw_text.split()
        if not tokens:
            return "❌ 형식: *** [슬롯명] [키/토큰/모델/프롬프트...]"
        
        slot_name = tokens[0]
        if slot_name not in slots:
            return f"❌ 슬롯을 못 찾았어. 사용 가능: {', '.join(slots.keys())}"
        
        rest = tokens[1:]
        if not rest:
            s = slots[slot_name]
            return f"ℹ️ [{slot_name}] 현재 설정:\n- 제공업체: {s.get('provider')}\n- 모델: {s['model']}\n- 키: {'등록됨' if s['apiKey'] else '없음'}\n- 토큰: {s['maxTokens']}"
        
        for t in rest:
            # 키 접두사별 제공업체 자동 판별
            if t.startswith("gsk_"):
                slots[slot_name]["apiKey"] = t
                slots[slot_name]["provider"] = "groq"
            elif t.startswith("sk-or-"):
                slots[slot_name]["apiKey"] = t
                slots[slot_name]["provider"] = "openrouter"
            elif t.startswith("deepinfra-") or (len(t) == 32 and not t.startswith("gsk_")):
                # DeepInfra 키 인식 (deepinfra- 로 시작하거나 32자리 키)
                slots[slot_name]["apiKey"] = t
                slots[slot_name]["provider"] = "deepinfra"
            elif t.isdigit():
                slots[slot_name]["maxTokens"] = int(t)
            elif "/" in t: # 모델명 지정 (예: deepseek/deepseek-r1, meta-llama/llama-3.3-70b-instruct)
                slots[slot_name]["model"] = t
            else:
                slots[slot_name]["sysPrompt"] = t

        s = slots[slot_name]
        return f"✅ [{s_name}] 설정 완료! (제공업체: {s['provider']}, 키: {s['apiKey'][:8]}...)"

    if "청소해" in text or "지워줘" in text:
        chat_history.clear()
        return "🗑️ 대화 기록을 지웠습니다."
    
    return None

# 2. 도구 실행
async def execute_tool(tool_name, args):
    if tool_name == "tavily_search":
        return f"[검색 결과] '{args.get('query')}' 관련 정보 수집 완료"
    elif tool_name == "get_app_diagnostics":
        try:
            with open("index.html", "r", encoding="utf-8") as f:
                return json.dumps({"source_code": f.read()})
        except Exception as e:
            return json.dumps({"error": str(e)})
    return "알 수 없는 도구 요청입니다."

# 3. AI 호출 및 스트리밍 (도구 피드백 루프 포함)
async def process_chat(ws, user_msg):
    global chat_history, slots, current_target
    
    if "탱탱" in user_msg: current_target = "탱탱"
    elif "호" in user_msg: current_target = "호"
    
    active_slot = slots.get(current_target)
    if not active_slot or not active_slot.get("apiKey"):
        await ws.send_json({"type": "system", "content": f"⚠️ [{current_target}]의 API 키가 없습니다."})
        return

# Provider별 API 주소 전체 세팅
    provider_urls = {
        "groq": "https://api.groq.com/openai/v1/chat/completions",
        "openrouter": "https://openrouter.ai/api/v1/chat/completions",
        "deepinfra": "https://api.deepinfra.com/v1/openai/chat/completions",
        "cerebras": "https://api.cerebras.ai/v1/chat/completions",
        "together": "https://api.together.xyz/v1/chat/completions"
    }
    url = provider_urls.get(active_slot.get("provider", "groq"))
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {active_slot['apiKey']}"}
    
    sys_prompt = active_slot.get("sysPrompt") or f"Feel free to use tools sequentially as many times as needed in a single turn. 너 이름은 '{current_target}'이고 무조건 반말해."
    
    messages = [{"role": "system", "content": sys_prompt}] + chat_history + [{"role": "user", "content": user_msg}]
    
    tools = [
        {"type": "function", "function": {"name": "tavily_search", "description": "실시간 검색", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "get_app_diagnostics", "description": "현재 HTML 소스코드 진단 및 검토", "parameters": {"type": "object", "properties": {"mode": {"type": "string"}}}}}
    ]

    async with aiohttp.ClientSession() as session:
        while True:
            body = {
                "model": active_slot.get("model"),
                "max_tokens": active_slot.get("maxTokens", 4096),
                "messages": messages,
                "tools": tools,
                "stream": True
            }

            async with session.post(url, json=body, headers=headers) as resp:
                if resp.status != 200:
                    await ws.send_json({"type": "system", "content": f"API Error: {resp.status}"})
                    return

                full_content = ""
                tool_calls = []

                async for line in resp.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]": break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            
                            if delta.get("content"):
                                chunk = delta["content"]
                                full_content += chunk
                                await ws.send_json({"type": "stream_chunk", "content": chunk})
                            
                            if delta.get("tool_calls"):
                                for tc in delta["tool_calls"]:
                                    idx = tc.get("index", 0)
                                    while len(tool_calls) <= idx:
                                        tool_calls.append({"id": "", "function": {"name": "", "arguments": ""}})
                                    if tc.get("id"): tool_calls[idx]["id"] = tc["id"]
                                    if tc.get("function", {}).get("name"): tool_calls[idx]["function"]["name"] = tc["function"]["name"]
                                    if tc.get("function", {}).get("arguments"): tool_calls[idx]["function"]["arguments"] += tc["function"]["arguments"]
                        except json.JSONDecodeError: pass

                # 도구 요청이 들어온 경우: 실행 후 AI에 결과값 넘겨주고 재호출
                if tool_calls:
                    assistant_msg = {"role": "assistant", "content": full_content or None, "tool_calls": []}
                    for tc in tool_calls:
                        assistant_msg["tool_calls"].append({
                            "id": tc["id"], "type": "function",
                            "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}
                        })
                    messages.append(assistant_msg)

                    for tc in tool_calls:
                        name = tc["function"]["name"]
                        args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
                        await ws.send_json({"type": "system", "content": f"🛠️ 도구 실행 중: [{name}]"})
                        res = await execute_tool(name, args)
                        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": res})
                    continue # 결과를 들고 다시 AI에 요청
                
                # 도구 호출 없이 최종 답변이 끝난 경우
                chat_history.append({"role": "user", "content": user_msg})
                if full_content: chat_history.append({"role": "assistant", "content": full_content})
                await ws.send_json({"type": "done", "content": ""})
                break

# 4. WebSocket 서버 설정
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
                if data.get("type") == "user_input":
                    content = data.get("content", "")
                    cmd_res = handle_command(content)
                    if cmd_res:
                        await ws.send_json({"type": "system", "content": cmd_res})
                    else:
                        await process_chat(ws, content)
                elif data.get("type") == "update_setting":
                    if "maxTokens" in data:
                        for s in slots: slots[s]["maxTokens"] = data["maxTokens"]
            except Exception as e:
                await ws.send_json({"type": "system", "content": f"Error: {str(e)}"})
    return ws

async def index(request):
    with open("index.html", "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html", charset="utf-8") # 👈 요렇게 분리!

app = web.Application()
app.router.add_get("/", index)
app.router.add_get("/ws", websocket_handler)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=5000)
