def generate():
            nonlocal api_key, provider, model, max_tokens, packed_messages, html_source
            
            try:
                # 최대 5번까지 연속 도구 호출 허용 (무한 루프 방지 안전장치)
                max_tool_iterations = 5
                iteration = 0
                
                full_content = ""
                full_reasoning = ""
                
                while iteration < max_tool_iterations:
                    iteration += 1
                    
                    # 1. AI 호출
                    response = call_ai_stream(api_key, provider, model, max_tokens, packed_messages, my_tools)
                    
                    tool_calls_acc = []
                    
                    for line in response:
                        line = line.decode("utf-8", errors="replace").strip()
                        if not line or line.startswith(":") or line == "data: [DONE]":
                            continue
                        
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
                                        while len(tool_calls_acc) <= idx:
                                            tool_calls_acc.append({
                                                "id": "",
                                                "type": "function",
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
                    
                    # 2. 도구 호출이 없으면 종료
                    if not tool_calls_acc or provider == "cerebras":
                        break
                    
                    # 3. 도구 실행 및 대화내역 저장
                    tool_call = tool_calls_acc[0]
                    tool_name = tool_call["function"]["name"]
                    
                    tool_args = {}
                    try:
                        tool_args = json.loads(tool_call["function"]["arguments"])
                    except:
                        pass
                    
                    yield f"data: {json.dumps({'choices': [{'delta': {'content': f'🛠️ [{iteration}회차] AI가 도구를 호출합니다: [{tool_name}]\\n'}, 'finish_reason': None}]})}\n\n"
                    
                    tool_result = execute_tool(tool_name, tool_args, html_source)
                    
                    packed_messages.append({
                        "role": "assistant",
                        "tool_calls": tool_calls_acc
                    })
                    packed_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": tool_result
                    })
                    
                    yield f"data: {json.dumps({'choices': [{'delta': {'content': f'🔍 [{iteration}회차] 도구 실행 결과 습득 완료, 계속 분석 중...\\n'}, 'finish_reason': None}]})}\n\n"
                
                yield "data: [DONE]\n\n"
                
                # DB/메모리에 최종 대화 저장
                if full_content:
                    global messages
                    if user_messages:
                        user_msg_content = user_messages[-1].get("content", "") if user_messages else ""
                        messages.append({"role": "user", "content": user_msg_content})
                    
                    messages.append({
                        "role": "assistant",
                        "content": full_content,
                        "reasoning": full_reasoning,
                        "name": current_target
                    })
                    if len(messages) > 50:
                        messages = messages[-50:]
                    save_history(messages)
                    
            except Exception as e:
                yield f"data: {json.dumps({'choices': [{'delta': {'content': f'❌ 에러: {str(e)}\n{traceback.format_exc()}'}, 'finish_reason': 'stop'}]})}\n\n"
                yield "data: [DONE]\n\n"
