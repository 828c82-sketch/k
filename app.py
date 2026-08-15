
import os
import json
import requests
import re
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ===== 환경변수 (서버 전용 키) =====
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
JINA_API_KEY = os.environ.get("JINA_API_KEY", "")

# ===== 현재 서버 소스코드 =====
SERVER_SOURCE_PATH = os.path.abspath(__file__)

# ===== [도구 1] 파이썬 백엔드 소스 읽기 =====
def read_python_source(start_line: int = None, end_line: int = None):
    try:
        with open(SERVER_SOURCE_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        
        if start_line is not None or end_line is not None:
            s = (start_line or 1) - 1
            e = end_line or total_lines
            s = max(0, s)
            e = min(total_lines, e)
            selected = lines[s:e]
            source = "".join(selected)
            return json.dumps({
                "filename": SERVER_SOURCE_PATH,
                "total_lines": total_lines,
                "start_line": s + 1,
                "end_line": e,
                "content": source
            }, ensure_ascii=False)
        else:
            source = "".join(lines)
            return json.dumps({
                "filename": SERVER_SOURCE_PATH,
                "total_lines": total_lines,
                "start_line": 1,
                "end_line": total_lines,
                "content": source
            }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"파일 읽기 실패: {str(e)}"})

# ===== [도구 2] 프론트엔드 HTML/JS 소스 읽기 (수정: 200줄 제한 제거) =====
def read_source_code(html_source: str = "", target_url: str = "", 
                     start_line: int = None, end_line: int = None,
                     keyword: str = "", lines_before: int = 3, lines_after: int = 3):
    """
    프론트엔드 HTML/JS 소스 또는 외부 URL을 읽어옵니다.
    end_line 미지정 시 전체를 읽고, start_line/end_line 지정 시 해당 범위만 읽습니다.
    """
    result = {"source": "", "type": "", "info": {}}
    
    # 1. URL 우선 처리
    if target_url:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
            resp = requests.get(target_url, headers=headers, timeout=30)
            text = resp.text
            lines = text.split("\n")
            total = len(lines)
            result["type"] = "url"
            result["info"] = {"url": target_url, "total_lines": total}
            
            # 키워드 검색
            if keyword:
                match_indices = []
                lower_keyword = keyword.lower()
                for i, line in enumerate(lines):
                    if lower_keyword in line.lower():
                        match_indices.append(i)
                
                if match_indices:
                    extracted = []
                    for idx in match_indices:
                        s = max(0, idx - lines_before)
                        e = min(total, idx + lines_after + 1)
                        extracted.append(f"--- 라인 {s+1}-{e} (매칭 라인 {idx+1}) ---")
                        extracted.extend(lines[s:e])
                    result["source"] = "\n".join(extracted)
                    result["info"]["matches"] = len(match_indices)
                else:
                    # 범위가 지정되면 해당 범위만, 없으면 전체
                    s = (start_line or 1) - 1
                    e = end_line or total  # 수정: min(total, 200) -> total
                    s = max(0, s)
                    e = min(total, e)
                    result["source"] = "\n".join(lines[s:e])
                    result["info"]["range"] = f"{s+1}-{e}"
            else:
                s = (start_line or 1) - 1
                e = end_line or total  # 수정: min(total, 200) -> total
                s = max(0, s)
                e = min(total, e)
                result["source"] = "\n".join(lines[s:e])
                result["info"]["range"] = f"{s+1}-{e}"
                
        except Exception as e:
            result["source"] = f"URL 읽기 실패: {str(e)}"
            result["type"] = "error"
    
    # 2. HTML 소스 직접 처리
    elif html_source:
        lines = html_source.split("\n")
        total = len(lines)
        result["type"] = "html_source"
        result["info"] = {"total_lines": total}
        
        if keyword:
            match_indices = []
            lower_keyword = keyword.lower()
            for i, line in enumerate(lines):
                if lower_keyword in line.lower():
                    match_indices.append(i)
            
            if match_indices:
                extracted = []
                for idx in match_indices:
                    s = max(0, idx - lines_before)
                    e = min(total, idx + lines_after + 1)
                    extracted.append(f"--- 라인 {s+1}-{e} (매칭 라인 {idx+1}) ---")
                    extracted.extend(lines[s:e])
                result["source"] = "\n".join(extracted)
                result["info"]["matches"] = len(match_indices)
            else:
                s = (start_line or 1) - 1
                e = end_line or total  # 수정: min(total, 200) -> total
                s = max(0, s)
                e = min(total, e)
                result["source"] = "\n".join(lines[s:e])
                result["info"]["range"] = f"{s+1}-{e}"
        else:
            s = (start_line or 1) - 1
            e = end_line or total  # 수정: min(total, 200) -> total
            s = max(0, s)
            e = min(total, e)
            result["source"] = "\n".join(lines[s:e])
            result["info"]["range"] = f"{s+1}-{e}"
    else:
        result["source"] = "읽을 내용이 없습니다. html_source 또는 target_url을 제공해주세요."
        result["type"] = "empty"
    
    return json.dumps(result, ensure_ascii=False)

# ===== 네이버 검색 =====
def naver_search(query: str, search_type: str = "blog"):
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return json.dumps({"error": "네이버 API 키가 설정되지 않았습니다."}, ensure_ascii=False)
    
    url = f"https://openapi.naver.com/v1/search/{search_type}.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    params = {"query": query, "display": 5, "sort": "sim"}
    
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        data = resp.json()
        
        if not data.get("items"):
            return json.dumps({"result": f"네이버 [{search_type}] 검색 결과가 없습니다."}, ensure_ascii=False)
        
        results = []
        for item in data["items"]:
            title = re.sub(r"<[^>]+>", "", item["title"])
            desc = re.sub(r"<[^>]+>", "", item.get("description", ""))
            link = item.get("link") or item.get("originallink", "")
            results.append({
                "title": title,
                "description": desc,
                "link": link
            })
        
        return json.dumps({
            "type": search_type,
            "query": query,
            "results": results
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"네이버 검색 실패: {str(e)}"}, ensure_ascii=False)

# ===== 타빌리 검색 =====
def tavily_search(query: str):
    if not TAVILY_API_KEY:
        return json.dumps({"error": "Tavily API 키가 설정되지 않았습니다."}, ensure_ascii=False)
    
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_API_KEY, "query": query, "max_results": 3},
            timeout=15
        )
        data = resp.json()
        
        if not data.get("results"):
            return json.dumps({"result": "검색 결과가 없습니다."}, ensure_ascii=False)
        
        results = []
        for item in data["results"]:
            results.append({
                "title": item["title"],
                "url": item["url"],
                "content": item["content"][:300]
            })
        
        return json.dumps({"query": query, "results": results}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"타빌리 검색 실패: {str(e)}"}, ensure_ascii=False)

# ===== Jina Reader =====
def jina_reader(url_or_query: str):
    if not JINA_API_KEY:
        return json.dumps({"error": "Jina API 키가 설정되지 않았습니다."}, ensure_ascii=False)
    
    is_url = url_or_query.startswith(("http://", "https://"))
    
    try:
        if is_url:
            fetch_url = f"https://r.jina.ai/{url_or_query}"
            label = "url"
        else:
            fetch_url = f"https://s.jina.ai/{url_or_query}"
            label = "search"
        
        headers = {"Authorization": f"Bearer {JINA_API_KEY}"}
        resp = requests.get(fetch_url, headers=headers, timeout=30)
        text = resp.text[:7000]
        
        return json.dumps({
            "type": label,
            "input": url_or_query,
            "content": text
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Jina 작업 실패: {str(e)}"}, ensure_ascii=False)

# ===== 도구 정의 =====
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_python_source",
            "description": "현재 서버에서 실행 중인 파이썬 백엔드 소스 파일(app.py)의 내용을 읽어옵니다. start_line, end_line으로 특정 라인 범위만 읽을 수 있습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_line": {
                        "type": "integer",
                        "description": "읽기 시작할 라인 번호 (1부터 시작)"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "읽을 마지막 라인 번호"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_source_code",
            "description": "프론트엔드 HTML/JS 소스 또는 외부 URL의 코드를 읽어옵니다. 라인 범위 지정, 키워드 검색 등 세밀한 읽기가 가능합니다. end_line 미지정 시 전체를 읽습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "html_source": {
                        "type": "string",
                        "description": "프론트엔드에서 전달받은 HTML/JS 소스 텍스트"
                    },
                    "target_url": {
                        "type": "string",
                        "description": "읽어올 외부 URL"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "읽기 시작할 라인 번호"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "읽을 마지막 라인 번호 (미지정 시 전체)"
                    },
                    "keyword": {
                        "type": "string",
                        "description": "검색할 키워드 (주변 라인 포함 출력)"
                    },
                    "lines_before": {
                        "type": "integer",
                        "description": "키워드 앞에 포함할 라인 수 (기본: 3)"
                    },
                    "lines_after": {
                        "type": "integer",
                        "description": "키워드 뒤에 포함할 라인 수 (기본: 3)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "naver_search",
            "description": "네이버 API로 한국어 콘텐츠(블로그, 뉴스, 지식iN, 웹문서, 카페글)를 검색합니다. 한국어 검색에 최적화되어 있습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색 키워드"
                    },
                    "search_type": {
                        "type": "string",
                        "enum": ["blog", "news", "kin", "webkr", "cafearticle"],
                        "description": "검색 유형 (기본: blog)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tavily_search",
            "description": "실시간 인터넷 검색을 수행합니다. 최신 뉴스, 날씨 등 실시간 정보가 필요할 때 사용합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색 키워드"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jina_reader",
            "description": "Jina AI를 이용해 웹 페이지의 전체 내용을 읽거나 웹 검색을 수행합니다. URL 전달 시 페이지 전체 텍스트 추출, 검색어 전달 시 웹 검색 결과를 반환합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url_or_query": {
                        "type": "string",
                        "description": "읽을 URL (http/https 시작) 또는 검색어"
                    }
                },
                "required": ["url_or_query"]
            }
        }
    }
]

# ===== OpenAI 호환 엔드포인트 =====
@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "요청 바디가 없습니다."}), 400
        
        api_key = data.get("api_key", "")
        model = data.get("model", "gpt-3.5-turbo")
        max_tokens = data.get("max_tokens", 4096)
        messages = data.get("messages", [])
        html_source = data.get("html_source", "")
        stream = data.get("stream", False)
        
        if not api_key:
            return jsonify({"error": "API 키가 없습니다."}), 400
        if not messages:
            return jsonify({"error": "메시지가 없습니다."}), 400
        
        # Provider 감지
        provider = "openrouter"
        provider_urls = {
            "groq": "https://api.groq.com/openai/v1/chat/completions",
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            "deepinfra": "https://api.deepinfra.com/v1/openai/chat/completions",
            "together": "https://api.together.xyz/v1/chat/completions"
        }
        
        for p, keyword in [("groq", "gsk_"), ("openrouter", "sk-or"), ("deepinfra", "lQ"), ("together", "Cdx")]:
            if api_key.startswith(keyword):
                provider = p
                break
        
        url = provider_urls.get(provider, "https://openrouter.ai/api/v1/chat/completions")
        
        # ===== 도구 실행기 =====
        def execute_tool(tool_name, tool_args):
            if tool_name == "read_python_source":
                return read_python_source(
                    start_line=tool_args.get("start_line"),
                    end_line=tool_args.get("end_line")
                )
            elif tool_name == "read_source_code":
                return read_source_code(
                    html_source=html_source,
                    target_url=tool_args.get("target_url", ""),
                    start_line=tool_args.get("start_line"),
                    end_line=tool_args.get("end_line"),
                    keyword=tool_args.get("keyword", ""),
                    lines_before=tool_args.get("lines_before", 3),
                    lines_after=tool_args.get("lines_after", 3)
                )
            elif tool_name == "naver_search":
                return naver_search(
                    query=tool_args.get("query", ""),
                    search_type=tool_args.get("search_type", "blog")
                )
            elif tool_name == "tavily_search":
                return tavily_search(tool_args.get("query", ""))
            elif tool_name == "jina_reader":
                return jina_reader(tool_args.get("url_or_query", ""))
            return json.dumps({"error": "알 수 없는 도구입니다."})
        
        # ===== AI 호출 함수 (수정: cerebras 조건 제거) =====
        def call_ai(messages_for_ai, tools_included=True):
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": request.host_url or "http://localhost:5000",
                "X-Title": "AI Chat Backend"
            }
            
            body = {
                "model": model,
                "messages": messages_for_ai,
                "max_tokens": max_tokens,
                "stream": stream
            }
            
            # 수정: provider != "cerebras" 조건 제거 -> tools_included가 True면 항상 tools 포함
            if tools_included:
                body["tools"] = TOOLS
            
            return requests.post(url, headers=headers, json=body, stream=stream, timeout=120)
        
        # ===== 스트리밍 처리 =====
        if stream:
            return Response(
                stream_with_context(handle_stream_with_tools(
                    call_ai(messages), html_source, messages, call_ai, execute_tool
                )),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        
        # ===== 일반 응답 (도구 호출 포함, 수정: cerebras 조건 제거) =====
        result = call_ai(messages).json()
        
        for _ in range(10):
            choice = result.get("choices", [{}])[0]
            message = choice.get("message", {})
            
            if "tool_calls" not in message or not message["tool_calls"]:
                break
            
            # 도구 실행
            tool_results = []
            for tc in message["tool_calls"]:
                func_name = tc["function"]["name"]
                func_args = json.loads(tc["function"]["arguments"]) if tc["function"].get("arguments") else {}
                result_text = execute_tool(func_name, func_args)
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text
                })
            
            messages.append(message)
            messages.extend(tool_results)
            
            result = call_ai(messages, tools_included=False).json()
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===== 스트리밍 처리 (수정: [DONE] 순서 변경) =====
def handle_stream_with_tools(initial_response, html_source, original_messages, call_ai, execute_tool):
    """SSE 스트리밍 + 도구 호출 루프
    [DONE]은 도구 호출 처리까지 완료된 후 최종 시점에만 보냅니다.
    """
    buffer = ""
    tool_calls_acc = {}
    full_content = ""
    full_reasoning = ""
    
    # 1차 스트리밍 파싱
    for chunk in initial_response.iter_content(chunk_size=1, decode_unicode=True):
        if chunk:
            buffer += chunk
            if "\n" in buffer:
                lines = buffer.split("\n")
                for line in lines[:-1]:
                    line = line.strip()
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            # 수정: [DONE] 받았지만 tool_calls_acc가 있으면 먼저 처리
                            if tool_calls_acc:
                                yield from process_tool_calls_and_resume(
                                    tool_calls_acc, full_content, full_reasoning,
                                    original_messages, call_ai, execute_tool, html_source
                                )
                            # 모든 처리 완료 후 최종 [DONE]
                            yield "data: [DONE]\n\n"
                            return
                        
                        try:
                            parsed = json.loads(data_str)
                            choices = parsed.get("choices", [])
                            if choices:
                                choice = choices[0]
                                delta = choice.get("delta", {})
                                
                                # 추론 과정 (DeepSeek 등)
                                reasoning = delta.get("reasoning") or delta.get("reasoning_content") or ""
                                if reasoning:
                                    full_reasoning += reasoning
                                    yield f"data: {json.dumps({'choices': [{'delta': {'reasoning': reasoning}}]})}\n\n"
                                
                                # 일반 내용
                                content = delta.get("content", "")
                                if content:
                                    full_content += content
                                    yield f"data: {json.dumps({'choices': [{'delta': {'content': content}}]})}\n\n"
                                
                                # 도구 호출
                                if "tool_calls" in delta:
                                    for tc in delta["tool_calls"]:
                                        idx = tc.get("index", 0)
                                        if idx not in tool_calls_acc:
                                            tool_calls_acc[idx] = {
                                                "id": tc.get("id", ""),
                                                "type": tc.get("type", "function"),
                                                "function": {"name": "", "arguments": ""}
                                            }
                                        if tc.get("id"):
                                            tool_calls_acc[idx]["id"] = tc["id"]
                                        if tc.get("function"):
                                            if tc["function"].get("name"):
                                                tool_calls_acc[idx]["function"]["name"] = tc["function"]["name"]
                                            if tc["function"].get("arguments"):
                                                tool_calls_acc[idx]["function"]["arguments"] += tc["function"]["arguments"]
                                
                                # finish_reason 체크
                                if choice.get("finish_reason") == "tool_calls":
                                    yield from process_tool_calls_and_resume(
                                        tool_calls_acc, full_content, full_reasoning,
                                        original_messages, call_ai, execute_tool, html_source
                                    )
                                    # 수정: 도구 호출 처리 후에도 [DONE] 보냄
                                    yield "data: [DONE]\n\n"
                                    return
                                    
                        except json.JSONDecodeError:
                            pass
                buffer = lines[-1]

def process_tool_calls_and_resume(tool_calls_acc, accumulated_content, accumulated_reasoning,
                                   original_messages, call_ai, execute_tool, html_source):
    """도구 호출 실행 후 AI 재요청"""
    tool_messages = []
    tool_calls_list = []
    
    for idx in sorted(tool_calls_acc.keys()):
        tc = tool_calls_acc[idx]
        args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
        
        # 도구 실행
        tool_result = execute_tool(tc["function"]["name"], args)
        tool_name = tc["function"]["name"]
        
        yield f"data: {json.dumps({'choices': [{'delta': {'content': f'[도구 호출: {tool_name}]'}}]})}\n\n"
        
        tool_calls_list.append(tc)
        tool_messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": tool_result
        })
    
    # AI 재요청 준비
    messages_copy = list(original_messages)
    if accumulated_content or accumulated_reasoning:
        assistant_msg = {"role": "assistant", "content": accumulated_content}
        if accumulated_reasoning:
            assistant_msg["reasoning"] = accumulated_reasoning
        if tool_calls_list:
            assistant_msg["tool_calls"] = tool_calls_list
        messages_copy.append(assistant_msg)
    else:
        messages_copy.append({
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls_list
        })
    
    messages_copy.extend(tool_messages)
    
    # 재요청 스트리밍
    retry_response = call_ai(messages_copy, tools_included=False)
    
    # 2차 스트리밍 파싱 및 출력
    buffer2 = ""
    for chunk in retry_response.iter_content(chunk_size=1, decode_unicode=True):
        if chunk:
            buffer2 += chunk
            if "\n" in buffer2:
                lines = buffer2.split("\n")
                for line in lines[:-1]:
                    line = line.strip()
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            # 수정: 2차 스트리밍에서 [DONE]은 여기서 yield하지 않고
                            # process_tool_calls_and_resume 호출한 쪽에서 처리
                            return
                        yield line + "\n\n"
                buffer2 = lines[-1]

# ===== 헬스 체크 =====
@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "server": "Flask AI Backend", "version": "2.1"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)


