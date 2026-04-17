content = open('E:/WorkSpace/nanobot-webui/web-ui/src/pages/ChatPage.tsx', 'r', encoding='utf-8').read()

# 1. Add streamingAssistantIdRef tracking in handleWsMessage done handler
old1 = "          if (assistantMsg) {\n            return [...withoutTemp, assistantMsg]\n          }"
new1 = "          if (assistantMsg) {\n            streamingAssistantIdRef.current = assistantMsg.id\n            return [...withoutTemp, assistantMsg]\n          }"
if old1 in content:
    content = content.replace(old1, new1, 1)
    print("1. OK - streamingAssistantIdRef tracking added")
else:
    print("1. NOT FOUND")

# 2. Reset streamingAssistantIdRef in handleSend before adding temp message
old2 = "    const tempUserMsg: Message = {"
new2 = "    streamingAssistantIdRef.current = null  // Reset before new stream\n    const tempUserMsg: Message = {"
if old2 in content:
    content = content.replace(old2, new2, 1)
    print("2. OK - streamingAssistantIdRef reset added")
else:
    print("2. NOT FOUND")

# 3. In processStreamEvent, skip message_start if ID already tracked
old3 = "      } else if (evt.type === 'message_start' && evt.role === 'assistant' && evt.content !== undefined) {"
new3 = "      } else if (evt.type === 'message_start' && evt.role === 'assistant' && evt.content !== undefined) {\n        // Skip if already added by done event (done sets streamingAssistantIdRef)\n        if (streamingAssistantIdRef.current && evt.content === undefined) { return }\n        if (streamingAssistantIdRef.current) { return }"
if old3 in content:
    content = content.replace(old3, new3, 1)
    print("3. OK - message_start duplicate prevention added")
else:
    print("3. NOT FOUND - checking alternative")
    # Try alternative
    idx = content.find("evt.type === 'message_start'")
    if idx > 0:
        print(f"   Found message_start at index {idx}")
        snippet = content[idx:idx+100]
        print(f"   Snippet: {repr(snippet)}")

open('E:/WorkSpace/nanobot-webui/web-ui/src/pages/ChatPage.tsx', 'w', encoding='utf-8').write(content)
print("Done")
