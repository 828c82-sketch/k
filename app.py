import os
import json
import asyncio
import aiohttp
from aiohttp import web

# ==============================================================================
# [공통 통신 규격 및 구조 정의]
# ==============================================================================
# - 프론트엔드: index.html
# - 백엔드: app.py (WebSocket 기반, 자동 저장 & 이전 대화 복원 기능 탑재)
# ==============================================================================

STATE_FILE = "server_state.json"

chat_history = []
current_target = "호"
slots = {
    "호": {"provider": "groq", "apiKey": "", "model": "llama-3.3-70b-versatile", "sysPrompt": "", "maxTokens": 4096},
    "탱탱": {"provider": "groq", "apiKey": "", "model": "qwen/qwen3.6-27b", "sysPrompt": "", "maxTokens": 4096}
}

# 상태 저장/복구
def save_state():
    try:
        data = {"slots": slots, "chat_history": chat_history, "current_target": current_target}
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"State save error: {e}")

def load_state():
    global slots, chat_history, current_target
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                slots = data.get("slots", slots)
                chat_history = data.get("chat_history", chat_history)
                current_target = data.get("current_target", current_target)
        except Exception as e:
            print(f"State load error: {e}")

load_state() # 서버 시작 시 이전 대화 및 설정 불러오기

# 1. 명령어 처리
def handle_command(text):

text = text.rstrip(".").strip() # ← 이거만 추가!
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
            if t.startswith("gsk_"):
                slots[slot_name]["apiKey"] = t
                slots[slot_name]["provider"] = "groq"
            elif t.startswith("sk-or-"):
                slots[slot_name]["apiKey"] = t
                slots[slot_name]["provider"] = "openrouter"
            elif t.startswith("deepinfra-") or (len(t) == 32 and not t.startswith("gsk_")):
                slots[slot_name]["apiKey"] = t
                slots[slot_name]["provider"] = "deepinfra"
            elif t.isdigit():
                slots[slot_name]["maxTokens"] = int(t)
            elif "/" in t:
                slots[slot_name]["model"] = t
            else:
                slots[slot_name]["sysPrompt"] = t

        save_state()
        s = slots[slot_name]
        return f"✅ [{slot_name}] 설정 완료! (제공업체: {s['provider']}, 키: {s['apiKey'][:8]}...)"

    if "청소해" in text or "지워줘" in text:
        chat_history.clear()
        save_state()
        return "🗑️ 대화 기록을 지웠습니다."
    
    return None

# 2. 코드 진단 도구
async def execute_tool(tool_name, args):
    if tool_name == "get_app_diagnostics":
        filename = args.get("filename", "index.html")
        keyword = args.get("keyword")
        context_lines = args.get("context_lines", 5)

        target_file = "app.py" if filename in ["app.py", "python", "backend"] else "index.html"

        try:
            if not os.path.exists(target_file):
                return json.dumps({"error": f"파일 '{target_file}'을 찾을 수 없습니다."})

            with open(target_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            if keyword:
                matched_results = []
                for idx, line in enumerate(lines):
                    if keyword.lower() in line.lower():
                        start = max(0, idx - context_lines)
                        end = min(len(lines), idx + context_lines + 1)
                        snippet = "".join(lines[start:end])
                        matched_results.append(f"--- [Line {start+1} ~ {end}] (키워드: '{keyword}') ---\n{snippet}")
                
                if matched_results:
                    return json.dumps({"file": target_file, "results": matched_results}, ensure_ascii=False)
                else:
                    return json.dumps({"file": target_file, "result": f"키워드 '{keyword}'를 찾지 못했습니다."}, ensure_ascii=False)

            return json.dumps({"file": target_file, "source_code": "".join(lines)}, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)})

    elif tool_name == "tavily_search":
        return f"[검색 결과] '{args.get('query')}' 관련 정보 수집 완료"
    
    return "알 수 없는 도구 요청입니다."

# 3. AI 호출 및 스트리밍
async def process_chat(ws, user_msg):
    global chat_history, slots, current_target
    
    if "탱탱" in user_msg: current_target = "탱탱"
    elif "호" in user_msg: current_target = "호"
    
    active_slot = slots.get(current_target)
    if not active_slot or not active_slot.get("apiKey"):
        await ws.send_json({"type": "system", "content": f"⚠️ [{current_target}]의 API 키가 없습니다."})
        return

    provider_urls = {
        "groq": "https://api.groq.com/openai/v1/chat/completions",
        "openrouter": "https://openrouter.ai/api/v1/chat/completions",
        "deepinfra": "https://api.deepinfra.com/v1/openai/chat/completions",
        "cerebras": "https://api.cerebras.ai/v1/chat/completions",
        "together": "https://api.together.xyz/v1/chat/completions"
    }
    url = provider_urls.get(active_slot.get("provider", "groq"))
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {active_slot['apiKey']}"}
    
    sys_prompt = active_slot.get("sysPrompt") or f"너 이름은 '{current_target}'이고 무조건 반말해. 필요시 get_app_diagnostics 도구를 사용해 소스코드를 직접 점검해라."
    messages = [{"role": "system", "content": sys_prompt}] + chat_history + [{"role": "user", "content": user_msg}]
    
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_app_diagnostics",
                "description": "HTML(index.html) 또는 Python(app.py) 소스코드를 검토합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "읽을 파일 ('index.html' 또는 'app.py')"},
                        "keyword": {"type": "string", "description": "찾을 특정 키워드"},
                        "context_lines": {"type": "integer", "description": "키워드 앞뒤 줄 수"}
                    }
                }
            }
        }
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
                            choices = data.get("choices", [])
                            if not choices: continue
                            
                            delta = choices[0].get("delta", {})
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
                        except Exception: pass

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
                        await ws.send_json({"type": "system", "content": f"🛠️ 도구 실행: [{name}]"})
                        res = await execute_tool(name, args)
                        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": res})
                    continue

                chat_history.append({"role": "user", "content": user_msg})
                if full_content: chat_history.append({"role": "assistant", "content": full_content})
                save_state()
                await ws.send_json({"type": "done", "content": ""})
                break

# 4. WebSocket 핸들러 (접속 즉시 이전 대화 내역 화면으로 복원)
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    # 🌟 핵심: 클라이언트가 연결되자마자 저장된 기존 대화 내역 뿌려주기
    if chat_history:
        for msg in chat_history:
            if msg.get("role") == "user":
                await ws.send_json({"type": "init_history", "role": "user", "content": msg["content"]})
            elif msg.get("role") == "assistant" and msg.get("content"):
                await ws.send_json({"type": "init_history", "role": "assistant", "content": msg["content"]})

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
                        save_state()
            except Exception as e:
                await ws.send_json({"type": "system", "content": f"Error: {str(e)}"})
    return ws

async def index(request):
    with open("index.html", "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html", charset="utf-8")

app = web.Application()
app.router.add_get("/", index)
app.router.add_get("/ws", websocket_handler)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=5000)
