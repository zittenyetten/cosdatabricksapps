async function streamSseJson(url, payload, handlers = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "text/event-stream"
    },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const responseText = await response.text();
    try {
      const parsed = JSON.parse(responseText);
      throw new Error(parsed.detail || parsed.message || `HTTP ${response.status}`);
    } catch (error) {
      if (error instanceof SyntaxError) {
        throw new Error(responseText || `HTTP ${response.status}`);
      }
      throw error;
    }
  }

  if (!response.body) {
    throw new Error("Streaming response body is not available.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const {value, done} = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, {stream: true});
    buffer = dispatchBufferedEvents(buffer, handlers);
  }

  buffer += decoder.decode();
  dispatchBufferedEvents(`${buffer}\n\n`, handlers);
}

function dispatchBufferedEvents(buffer, handlers) {
  let normalized = buffer.replace(/\r\n/g, "\n");
  let separatorIndex = normalized.indexOf("\n\n");

  while (separatorIndex !== -1) {
    const rawEvent = normalized.slice(0, separatorIndex);
    normalized = normalized.slice(separatorIndex + 2);
    separatorIndex = normalized.indexOf("\n\n");

    if (!rawEvent.trim()) {
      continue;
    }

    const {event, data} = parseSseEvent(rawEvent);
    handlers.onEvent?.(event, data);
    if (event === "final") {
      handlers.onFinal?.(data);
    }
    if (event === "error") {
      handlers.onError?.(data);
    }
  }

  return normalized;
}

function parseSseEvent(rawEvent) {
  const lines = rawEvent.split("\n");
  let event = "message";
  const dataLines = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  const rawData = dataLines.join("\n");
  let data = {};
  if (rawData) {
    try {
      data = JSON.parse(rawData);
    } catch {
      data = {raw: rawData};
    }
  }

  return {event, data};
}

window.CosbelleStream = {streamSseJson};
