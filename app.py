from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import json
import os
import re
import time
import urllib.request
import urllib.parse
import ssl
import traceback

app = Flask(__name__)
CORS(app)

# ──────────────────────────────────────────────
# 0. 전역 HTML 소스 (프론트엔드에서 업데이트)
# ──────────────────────────────────────────────
global_html_source = ""

# ──────────────────────────────────────────────
# 1. 슬롯 시스템 (파이썬 서버 메모리 + 파일 저장)
# ──────────────────────────────────────────────
SLOTS_FILE = "slots.json"

def load_slots():
    try:
        if os.path.exists(SLOTS_FILE):
            with open(SLOTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return {
        "송": {
            "provider": "groq",
            "apiKey": "",
            "model": "llama-3.3-70b-versatile",
            "sysPrompt": "",
            "maxTokens": 4096
        },
        "땡킹": {
            "provider": "groq",
            "apiKey": "",
            "model": "qwen/qwen3.6-27b",
            "sysPrompt": "",
            "maxTokens": 4096
        }
    }

def save_slots(slots):
    with open(SLOTS_FILE, "w", encoding="utf-8") as f:
        json.dump(slots, f, ensure_ascii=False, indent=2)

slots = load_slots()
current_target = "송"

# ──────────────────────────────────────────────
# 2. 채팅 히스토리 (파이썬 서버 메모리 + 파일 저장)
# ──────────────────────────────────────────────
HISTORY_FILE = "chat_history.json"

def load_history():
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return []

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

messages = load_history()

# ──────────────────────────────────────────────
# 3. 도구 정의
# ──────────────────────────────────────────────
my_tools = [
{
        "type": "function",
        "function": {
            "name": "get_app_diagnostics",
            "description": "파이썬 서버 소스코드 보기. [예시] 전체: mode='full' / 찾기: mode='extract', target='단어', before=0, after=0, case_sensitive=false, occurrence=0",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["full", "extract"], "default": "full", "description": "'full'=전체코드, 'extract'=키워드찾기"},
                    "target": {"type": "string", "description": "찾을 단어 (예: 'execute_tool', 'def ', 'send_message') mode='extract'일 때 필수"},
                    "lines_before": {"type": "number", "default": 3, "description": "찾은 줄 위 추가 줄 수 (0=딱 그 줄만)"},
                    "lines_after": {"type": "number", "default": 3, "description": "찾은 줄 아래 추가 줄 수 (0=딱 그 줄만)"},
                    "case_sensitive": {"type": "boolean", "default": False, "description": "true=대소문자 구분, false=무시"},
                    "occurrence": {"type": "number", "default": 0, "description": "0=첫번째, 1=두번째, 2=세번째... (같은 단어 여러 개)"}
                },
                "required": ["mode", "target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_html_source",
            "description": "HTML 소스코드 보기. [예시] 전체: mode='full' / 찾기: mode='extract', target='단어', before=0, after=0, case_sensitive=false, occurrence=0",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["full", "extract"], "default": "full", "description": "'full'=전체 HTML, 'extract'=키워드찾기"},
                    "target": {"type": "string", "description": "찾을 단어 (예: 'send', 'button', 'function') mode='extract'일 때 필수"},
                    "lines_before": {"type": "number", "default": 3, "description": "찾은 줄 위 추가 줄 수 (0=딱 그 줄만)"},
                    "lines_after": {"type": "number", "default": 3, "description": "찾은 줄 아래 추가 줄 수 (0=딱 그 줄만)"},
                    "case_sensitive": {"type": "boolean", "default": False, "description": "true=대소문자 구분, false=무시"},
                    "occurrence": {"type": "number", "default": 0, "description": "0=첫번째, 1=두번째, 2=세번째... (같은 단어 여러 개)"}
                },
                "required": ["mode", "target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tavily_search",
            "description": "실시간 인터넷 검색을 수행합니다. 최신 뉴스, 날씨, 검색어 등 실시간 정보가 필요할 때 사용해 주세요.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "검색할 키워드 또는 질문"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "naver_search",
            "description": "Searches Korean web content via Naver Open API. Best for Korean news, blog posts, Cafe, and Q&A (Kin).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword in Korean"},
                    "type": {"type": "string", "enum": ["blog", "news", "kin", "webkr", "cafearticle"], "description": "Type of search: 'blog' (default), 'news', 'kin', 'webkr', 'cafearticle'"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jina_reader",
            "description": "Fetches web content using Jina AI. If a URL is provided, it extracts full page text. If a search term is provided, it searches the web.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Target full URL (starting with http/https) to read OR search keyword to look up."}
                },
                "required": ["url"]
            }
        }
    }
]

# ──────────────────────────────────────────────
# 4. 도구 실행 함수
# ──────────────────────────────────────────────
def execute_tool(tool_name, args_obj, html_source=None):
    if tool_name == "get_app_diagnostics":
        current_source = open(__file__, "r", encoding="utf-8").read()
        mode = args_obj.get("mode", "full") if args_obj else "full"
        
        if mode == "full":
            return json.dumps({
                "system_info": "Python Flask AI Chat Server",
                "source_code": current_source
            }, ensure_ascii=False)
        
        target = args_obj.get("target", "") if args_obj else ""
        lines_before = args_obj.get("lines_before", 3) if args_obj else 3
        lines_after = args_obj.get("lines_after", 3) if args_obj else 3
        case_sensitive = args_obj.get("case_sensitive", False) if args_obj else False
        occurrence = args_obj.get("occurrence", 0) if args_obj else 0
        
        if not target:
            return json.dumps({"error": "target 파라미터가 필요합니다."})
        
        source_lines = current_source.split('\n')
        match_indices = []
        search_key = target if case_sensitive else target.lower()
        
        for i, line in enumerate(source_lines):
            check_line = line if case_sensitive else line.lower()
            if search_key in check_line:
                match_indices.append(i)
        
        if occurrence > 0 and match_indices:
            match_indices = [match_indices[occurrence - 1]]
        
        extracted_blocks = []
        for idx, line_idx in enumerate(match_indices):
            start = max(0, line_idx - lines_before)
            end = min(len(source_lines) - 1, line_idx + lines_after)
            extracted_blocks.append({
                "occurrence": idx + 1,
                "line_number": line_idx + 1,
                "context": '\n'.join(source_lines[start:end + 1])
            })
        
        result_msg = f"🎯 키워드 '{target}' 검색 완료:\n"
        result_msg += f"총 {len(match_indices)}개 발견 (요청: {'전체' if occurrence == 0 else str(occurrence) + '번째'}).\n"
        for block in extracted_blocks:
            result_msg += f"\n--- [{block['occurrence']}번째 매칭] (줄 {block['line_number']}) ---\n{block['context']}\n"
        
        return json.dumps({
            "system_info": "Python Flask AI Chat Server",
            "search_info": {"target": target, "total_matches": len(match_indices), "mode": "extract"},
            "extracted_source": result_msg
        }, ensure_ascii=False)
    
    elif tool_name == "read_html_source":
        if not html_source:
            return json.dumps({"error": "HTML 소스 코드가 제공되지 않았습니다. 프론트엔드에서 html_source 필드를 포함해주세요."})
        
        mode = args_obj.get("mode", "full") if args_obj else "full"
        
        if mode == "full":
            return json.dumps({
                "system_info": "HTML Frontend Source Code",
                "source_code": html_source
            }, ensure_ascii=False)
        
        target = args_obj.get("target", "") if args_obj else ""
        lines_before = args_obj.get("lines_before", 3) if args_obj else 3
        lines_after = args_obj.get("lines_after", 3) if args_obj else 3
        case_sensitive = args_obj.get("case_sensitive", False) if args_obj else False
        occurrence = args_obj.get("occurrence", 0) if args_obj else 0
        
        if not target:
            return json.dumps({"error": "target 파라미터가 필요합니다."})
        
        source_lines = html_source.split('\n')
        match_indices = []
        search_key = target if case_sensitive else target.lower()
        
        for i, line in enumerate(source_lines):
            check_line = line if case_sensitive else line.lower()
            if search_key in check_line:
                match_indices.append(i)
        
        if occurrence > 0 and match_indices:
            match_indices = [match_indices[occurrence - 1]]
        
        extracted_blocks = []
        for idx, line_idx in enumerate(match_indices):
            start = max(0, line_idx - lines_before)
            end = min(len(source_lines) - 1, line_idx + lines_after)
            extracted_blocks.append({
                "occurrence": idx + 1,
                "line_number": line_idx + 1,
                "context": '\n'.join(source_lines[start:end + 1])
            })
        
        result_msg = f"🎯 키워드 '{target}' 검색 완료:\n"
        result_msg += f"총 {len(match_indices)}개 발견 (요청: {'전체' if occurrence == 0 else str(occurrence) + '번째'}).\n"
        for block in extracted_blocks:
            result_msg += f"\n--- [{block['occurrence']}번째 매칭] (줄 {block['line_number']}) ---\n{block['context']}\n"
        
        return json.dumps({
            "system_info": "HTML Frontend Source Code",
            "search_info": {"target": target, "total_matches": len(match_indices), "mode": "extract"},
            "extracted_source": result_msg
        }, ensure_ascii=False)
    
    elif tool_name == "tavily_search":
        api_key = os.environ.get("TAVILY_API_KEY", "")
        query = args_obj.get("query", "") if args_obj else ""
        
        try:
            context = ssl._create_unverified_context()
            data = json.dumps({"api_key": api_key, "query": query, "max_results": 3}).encode("utf-8")
            req = urllib.request.Request(
                "https://api.tavily.com/search",
                data=data,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, context=context, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            
            summary = "🌐 [타빌리 실시간 검색 결과]\n"
            if result.get("results"):
                for idx, item in enumerate(result["results"]):
                    content = item.get("content", "")
                    summary += f"{idx + 1}. {item.get('title', '')}\n   {item.get('url', '')}\n   {content[:120]}...\n\n"
            else:
                summary += "일치하는 결과가 없거나, 현재 네트워크 상태가 원활하지 않아요."
            return summary
        except Exception as e:
            return f"🚫 타빌리 검색 실행 중 에러 발생: {str(e)}"
    
    elif tool_name == "naver_search":
        query = args_obj.get("query") if args_obj else None
        search_type = args_obj.get("type", "blog") if args_obj else "blog"
        
        if not query:
            return "오류: 검색어가 전달되지 않았습니다."
        
        client_id = os.environ.get("NAVER_CLIENT_ID", "")
        client_secret = os.environ.get("NAVER_CLIENT_SECRET", "")
        
        try:
            context = ssl._create_unverified_context()
            encoded_query = urllib.parse.quote(query)
            url = f"https://openapi.naver.com/v1/search/{search_type}.json?query={encoded_query}&display=5&sort=sim"
            
            req = urllib.request.Request(url)
            req.add_header("X-Naver-Client-Id", client_id)
            req.add_header("X-Naver-Client-Secret", client_secret)
            
            with urllib.request.urlopen(req, context=context, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            
            if not data.get("items"):
                return f"네이버 [{search_type}] 검색 결과가 없습니다."
            
            results = []
            for idx, item in enumerate(data["items"]):
                title = re.sub(r'<[^>]*>?', '', item.get("title", ""))
                description = re.sub(r'<[^>]*>?', '', item.get("description", ""))
                link = item.get("link") or item.get("originallink", "")
                results.append(f"[{idx + 1}] {title}\n- 요약: {description}\n- 링크: {link}")
            
            return f"네이버 [{search_type}] 검색 결과:\n\n" + "\n\n".join(results)
        
        except Exception as e:
            return f"네이버 검색 실패: {str(e)}"
    
    elif tool_name == "jina_reader":
        input_param = args_obj.get("url") if args_obj else None
        if not input_param:
            return "오류: URL 또는 검색어가 전달되지 않았습니다."
        
        is_url = bool(re.match(r'^https?://', input_param.strip(), re.I))
        
        try:
            context = ssl._create_unverified_context()
            
            if is_url:
                fetch_url = f"https://r.jina.ai/{input_param.strip()}"
            else:
                fetch_url = f"https://s.jina.ai/{urllib.parse.quote(input_param.strip())}"
            
            req = urllib.request.Request(fetch_url)
            req.add_header("Authorization", os.environ.get("JINA_API_KEY", ""))
            
            with urllib.request.urlopen(req, context=context, timeout=30) as resp:
                text = resp.read().decode("utf-8")
            
            if len(text) > 7000:
                text = text[:7000] + "\n\n(내용이 길어 일부 절삭됨)"
            return text
        
        except Exception as e:
            return f"Jina 작업 실패: {str(e)}"
    
    return "알 수 없는 도구 요청입니다."


# ──────────────────────────────────────────────
# 5. AI 호출 함수 (스트리밍)
# ──────────────────────────────────────────────
def call_ai_stream(api_key, provider, model, max_tokens, messages, tools):
    """AI API를 호출하고 스트리밍 응답을 생성"""
    
    if provider == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    elif provider == "openrouter":
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://pppp-2132.onrender.com",
            "X-Title": "AI Chat Server"
        }
    elif provider == "cerebras":
        url = "https://api.cerebras.ai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    elif provider == "together":
        url = "https://api.together.xyz/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    elif provider == "deepinfra":
        url = "https://api.deepinfra.com/v1/openai/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    else:
        raise ValueError(f"Unknown provider: {provider}")
    
    body = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
    }
    
    if provider == "cerebras":
        body.pop("max_tokens", None)
        body["max_completion_tokens"] = max_tokens
    else:
        body["tools"] = tools
    
    body = {k: v for k, v in body.items() if v is not None}
    
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    
    context = ssl._create_unverified_context()
    return urllib.request.urlopen(req, context=context, timeout=120)


# ──────────────────────────────────────────────
# 6. 메인 엔드포인트
# ──────────────────────────────────────────────
@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    global current_target, slots, messages, global_html_source
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "요청 본문이 없습니다."}), 400
        
        api_key = data.get("api_key", "")
        model = data.get("model", "")
        provider = data.get("provider", "groq")
        user_messages = data.get("messages", [])
        html_source = data.get("html_source", global_html_source)
        slot_name = data.get("slot_name", "")
        sys_prompt = data.get("sys_prompt", "")
        
        if user_messages and len(user_messages) > 0:
            last_msg = user_messages[-1].get("content", "")
            if last_msg.startswith("***"):
                result = handle_star_command(last_msg)
                return jsonify({"result": result, "type": "command"})
        
        if slot_name and slot_name in slots:
            current_target = slot_name
        
        target_slot = slots.get(current_target, slots["송"])
        
        if not api_key and target_slot.get("apiKey"):
            api_key = target_slot["apiKey"]
        if not model and target_slot.get("model"):
            model = target_slot["model"]
        if not provider and target_slot.get("provider"):
            provider = target_slot["provider"]
        if not sys_prompt and target_slot.get("sysPrompt"):
            sys_prompt = target_slot["sysPrompt"]
        
        # 🔥 수정: 클라이언트 → 슬롯 → 기본값 순서로 max_tokens 결정
        max_tokens = data.get("max_tokens", target_slot.get("maxTokens", 4096)) or 4096
        
        if not api_key:
            return jsonify({"error": f"API 키가 없습니다. *** {current_target} [API키] 로 등록해주세요."}), 400
        
        if not sys_prompt:
            sys_prompt = f"너 이름은 '{current_target}'이고 반말해."
        
        max_history = 16000
        sys_token = len(sys_prompt)
        
        total_token = sys_token
        packed_messages = [{"role": "system", "content": sys_prompt}]
        
        for msg in reversed(user_messages):
            msg_token = len(msg.get("content", ""))
            if total_token + msg_token < max_history:
                packed_messages.insert(1, msg)
                total_token += msg_token
            else:
                break
        
def generate():
nonlocal api_key, provider, model, max_tokens, packed_messages, html_source

try:
# --- [추가] 도구 실행 직전에 진단 정보 수집 ---
# 이 부분을 추가!

response = call_ai_stream(api_key, provider, model, max_tokens, packed_messages, my_tools)

full_content = ""
full_reasoning = ""
tool_calls_acc = []

for line in response:
line = line.decode("utf-8", errors="replace").strip()
if not line or line.startswith(":"):
continue
if line == "data: [DONE]":
break

if line.startswith("data: "):
json_str = line[6:]
try:
parsed = json.loads(json_str)
choices = parsed.get("choices", [])
if not choices:
continue

choice = choices[0]
delta = choice.get("delta", {})
if not delta:
continue

reasoning_chunk = delta.get("reasoning") or delta.get("reasoning_content") or ""
if reasoning_chunk:
full_reasoning += reasoning_chunk

content_chunk = delta.get("content", "")
if content_chunk:
full_content += content_chunk

tool_calls = delta.get("tool_calls", [])
if tool_calls:
for tc in tool_calls:
idx = tc.get("index", 0)
if idx >= len(tool_calls_acc):
tool_calls_acc.append({
"id": tc.get("id", ""),
"type": tc.get("type", "function"),
"function": {"name": "", "arguments": ""}
})
if tc.get("id"):
tool_calls_acc[idx]["id"] = tc["id"]
if tc.get("function"):
if tc["function"].get("name"):
tool_calls_acc[idx]["function"]["name"] = tc["function"]["name"]
if tc["function"].get("arguments"):
tool_calls_acc[idx]["function"]["arguments"] += tc["function"]["arguments"]

yield f"data: {json_str}\n\n"

except json.JSONDecodeError:
pass

# ---------- 도구 실행부 ----------
if tool_calls_acc and provider != "cerebras":
tool_call = tool_calls_acc[0]
tool_name = tool_call["function"]["name"]

tool_args = {}
try:
tool_args = json.loads(tool_call["function"]["arguments"])
except:
pass

yield f"data: {json.dumps({'choices': [{'delta': {'content': f'🛠️ AI가 도구를 호출합니다: [{tool_name}]\\n'}, 'finish_reason': None}]})}\n\n"

tool_result = execute_tool(tool_name, tool_args, html_source)

tool_messages = list(packed_messages)
tool_messages.append({
"role": "assistant",
"tool_calls": tool_calls_acc
})
tool_messages.append({
"role": "tool",
"tool_call_id": tool_call["id"],
"content": tool_result
})

yield f"data: {json.dumps({'choices': [{'delta': {'content': '🔍 도구 실행 결과를 분석 중...\\n'}, 'finish_reason': None}]})}\n\n"

try:
response2 = call_ai_stream(api_key, provider, model, max_tokens, tool_messages, [])

for line2 in response2:
line2 = line2.decode("utf-8", errors="replace").strip()
if not line2 or line2.startswith(":") or line2 == "data: [DONE]":
continue
if line2.startswith("data: "):
yield f"{line2}\n\n"

except Exception as e2:
yield f"data: {json.dumps({'choices': [{'delta': {'content': f'에러: {str(e2)}'}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"



# ──────────────────────────────────────────────
# 7. 명령어 처리 (***)
# ──────────────────────────────────────────────
def handle_star_command(raw_input):
    global slots, current_target
    
    tokens = re.sub(r'^\*\*\*\s*', '', raw_input).strip().split()
    if not tokens:
        return "❌ 형식: *** [슬롯명] [설정값들...]"
    
    if "지워줘" in raw_input:
        global messages
        messages = []
        save_history(messages)
        return "대화 기록이 삭제되었습니다."
    
    if "백업해" in raw_input:
        return backup_history()
    
    slot_names = list(slots.keys())
    target_name = None
    for t in tokens:
        if t in slot_names:
            target_name = t
            break
    
    if not target_name:
        return f"❌ 슬롯을 못 찾았어. 사용 가능: {', '.join(slot_names)}"
    
    rest = [t for t in tokens if t != target_name]
    
    if not rest:
        s = slots[target_name]
        api_key_status = "등록됨" if s.get("apiKey") else "없음"
        return f"ℹ️ [{target_name}] 현재 설정:\n- 모델: {s['model']}\n- 키: {api_key_status}\n- 토큰: {s.get('maxTokens', 4096)}\n- 프롬프트: {s.get('sysPrompt', '없음')}"
    
    for t in rest:
        if t.isdigit():
            slots[target_name]["maxTokens"] = int(t)
        elif t.startswith("gsk_"):
            slots[target_name]["apiKey"] = t
            slots[target_name]["provider"] = "groq"
        elif t.startswith("sk-or-v1-"):
            slots[target_name]["apiKey"] = t
            slots[target_name]["provider"] = "openrouter"
        elif t.startswith("lQ"):
            slots[target_name]["apiKey"] = t
            slots[target_name]["provider"] = "deepinfra"
        elif t.startswith("Cdx"):
            slots[target_name]["apiKey"] = t
            slots[target_name]["provider"] = "together"
        elif t.startswith("csk"):
            slots[target_name]["apiKey"] = t
            slots[target_name]["provider"] = "cerebras"
        elif "/" in t or "-" in t:
            slots[target_name]["model"] = t
        else:
            slots[target_name]["sysPrompt"] = t
    
    save_slots(slots)
    
    summary = [f"✅ [{target_name}] 슬롯 설정 업데이트 완료!"]
    if any(t.startswith(("gsk_", "sk-or-v1-", "lQ", "Cdx", "csk")) for t in rest):
        summary.append(f"🔑 API키 등록 완료 ({slots[target_name]['provider']})")
    if any("/" in t or "-" in t for t in rest):
        summary.append(f"🤖 모델: {slots[target_name]['model']}")
    
    return '\n'.join(summary)


def backup_history():
    global messages
    if not messages:
        return "⚠️ 백업할 대화 내용이 없습니다."
    
    export_text = "=== AI 채팅 대화 백업 ===\n\n"
    for m in messages:
        role_name = "나" if m["role"] == "user" else (m.get("name", "AI"))
        export_text += f"[{role_name}]\n"
        if m.get("reasoning"):
            export_text += f"<생각 과정>\n{m['reasoning']}\n</생각 과정>\n"
        export_text += f"{m['content']}\n\n-----------------------------------\n\n"
    
    today = time.strftime("%Y-%m-%d")
    filename = f"chat_backup_{today}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(export_text)
    
    return f"💾 대화 내용이 {filename}에 저장되었습니다!"


# ──────────────────────────────────────────────
# 8. 슬롯 관리 API (프론트에서 호출)
# ──────────────────────────────────────────────
@app.route("/v1/slots", methods=["GET", "POST"])
def manage_slots():
    global slots
    
    if request.method == "GET":
        safe_slots = {}
        for name, slot in slots.items():
            safe_slots[name] = {
                "provider": slot.get("provider"),
                "model": slot.get("model"),
                "maxTokens": slot.get("maxTokens"),
                "sysPrompt": slot.get("sysPrompt"),
                "apiKey": "등록됨" if slot.get("apiKey") else "없음"
            }
        return jsonify({
            "slots": safe_slots,
            "currentTarget": current_target
        })
    
    elif request.method == "POST":
        data = request.get_json()
        if data and "slots" in data:
            slots = data["slots"]
            save_slots(slots)
        if data and "currentTarget" in data:
            current_target = data["currentTarget"]
        return jsonify({"status": "ok"})


@app.route("/v1/history", methods=["GET", "POST", "DELETE"])
def manage_history():
    global messages
    
    if request.method == "GET":
        return jsonify({"messages": messages})
    
    elif request.method == "POST":
        data = request.get_json()
        if data and "messages" in data:
            messages = data["messages"]
            save_history(messages)
        return jsonify({"status": "ok"})
    
    elif request.method == "DELETE":
        messages = []
        save_history(messages)
        return jsonify({"status": "cleared"})


@app.route("/v1/switch", methods=["POST"])
def switch_slot():
    global current_target, slots
    
    data = request.get_json()
    target = data.get("target", "") if data else ""
    
    if target in slots:
        current_target = target
        return jsonify({"status": "ok", "currentTarget": current_target})
    else:
        return jsonify({"error": f"슬롯 '{target}'을 찾을 수 없습니다."}), 404


# ──────────────────────────────────────────────
# 9. HTML 소스 업데이트 엔드포인트
# ──────────────────────────────────────────────
@app.route("/v1/update_html", methods=["POST"])
def update_html():
    global global_html_source
    
    data = request.get_json()
    if not data or "html_source" not in data:
        return jsonify({"error": "html_source 필드가 필요합니다."}), 400
    
    global_html_source = data["html_source"]
    length = len(global_html_source)
    
    return jsonify({
        "status": "ok",
        "message": f"HTML 소스가 업데이트되었습니다. (길이: {length}자)"
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "slots": list(slots.keys()), "currentTarget": current_target})


# ──────────────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
