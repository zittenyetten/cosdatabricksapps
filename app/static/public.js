let publicRoles = [];

document.getElementById("publicChatForm").addEventListener("submit", async (event) => {
  event.preventDefault();

  const questionInput = document.getElementById("publicQuestion");
  const question = questionInput.value.trim();
  if (!question) {
    return;
  }

  if (question.toLowerCase() === "/clear") {
    questionInput.value = "";
    resetPublicChat();
    return;
  }

  const selectedRole = getSelectedPublicRole();
  appendPublicMessage("user", "User", question);
  questionInput.value = "";
  setPublicGuard("RUNNING", false);
  renderPublicSources({});

  const payload = {
    endpoint: "/api/answer",
    query: question,
    role_id: selectedRole.role_id,
    rbac_enabled: true,
    pre_check_enabled: true,
    post_check_enabled: true
  };
  const progressMessage = appendPublicProgressMessage();

  try {
    let finalData = null;
    let streamError = null;

    await window.CosbelleStream.streamSseJson("/api/chat/stream", payload, {
      onEvent: (eventName, eventPayload) => {
        updatePublicStreamProgress(progressMessage, eventName, eventPayload);
      },
      onFinal: (eventPayload) => {
        finalData = eventPayload;
      },
      onError: (eventPayload) => {
        streamError = eventPayload;
      }
    });

    progressMessage.remove();
    if (streamError) {
      throw new Error(streamError.detail || streamError.message || "Streaming request failed");
    }
    if (!finalData) {
      throw new Error("Streaming request finished without a final response.");
    }

    renderPublicResponse(finalData, selectedRole);
  } catch (error) {
    progressMessage.remove();
    setPublicGuard("ERROR", true);
    appendErrorMessage({
      answer: `요청 처리 중 오류가 발생했습니다: ${error.message}`,
      role_id: getSelectedPublicRole().role_id,
      checks: {pre_check: "ERROR", post_check: "ERROR"}
    });
    renderPublicSources({});
  }
});

document.getElementById("publicRoleSelect").addEventListener("change", () => {
  const selectedRole = getSelectedPublicRole();
  updatePublicIdentity({
    role_id: selectedRole.role_id,
    department_name: selectedRole.department,
    security_clearance: selectedRole.default_clearance
  });
  setPublicGuard("READY", false);
  renderPublicSources({});
});

document.getElementById("newPublicChat").addEventListener("click", () => {
  document.getElementById("publicQuestion").value = "";
  resetPublicChat();
});

async function parseJsonResponse(response) {
  const responseText = await response.text();
  try {
    return responseText ? JSON.parse(responseText) : {};
  } catch {
    throw new Error(responseText || "Invalid JSON response");
  }
}

function renderPublicResponse(data, selectedRole) {
  const identity = data.effective_identity || {
    role_id: selectedRole.role_id,
    department_name: selectedRole.department,
    security_clearance: selectedRole.default_clearance
  };
  updatePublicIdentity(identity);

  const resultKind = getResultKind(data);
  setPublicGuard(resultKind.status, resultKind.kind !== "success");

  if (resultKind.kind === "blocked") {
    appendBlockingMessage(data);
  } else if (resultKind.kind === "error") {
    appendErrorMessage(data);
  } else {
    appendPublicMessage("assistant markdown", "Assistant", data.answer || "", {
      status: resultKind.status,
      role: data.role_id || identity.role_id,
      clearance: data.security_clearance || identity.security_clearance
    });
  }

  renderPublicSources(data.sources || {});
}

function appendPublicProgressMessage() {
  const message = document.createElement("div");
  message.className = "chat-message assistant streaming";
  message.innerHTML = `
    <div class="message-meta">
      <span>Assistant</span>
      <strong class="status-pill success">RUNNING</strong>
    </div>
    <p>요청을 처리하고 있습니다.</p>
  `;
  appendMessageElement(message);
  return message;
}

function updatePublicStreamProgress(message, eventName, eventPayload) {
  if (eventName === "final") {
    return;
  }

  const status = streamEventStatus(eventName, eventPayload);
  setPublicGuard(status, ["BLOCKED", "DENIED", "ERROR"].includes(status));

  const badge = message.querySelector(".status-pill");
  const body = message.querySelector("p");
  if (badge) {
    badge.textContent = status;
    badge.classList.toggle("error", status === "ERROR");
    badge.classList.toggle("blocked", ["BLOCKED", "DENIED"].includes(status));
  }
  if (body) {
    body.textContent = describeStreamEvent(eventName, eventPayload);
  }
}

function streamEventStatus(eventName, eventPayload) {
  const payloadStatus = String(eventPayload?.status || "").toUpperCase();
  if (["BLOCKED", "DENIED", "ERROR"].includes(payloadStatus)) {
    return payloadStatus;
  }
  if (eventName === "error") {
    return "ERROR";
  }
  return "RUNNING";
}

function describeStreamEvent(eventName, eventPayload) {
  const labels = {
    accepted: "요청을 접수했습니다.",
    intent: `질문 유형을 확인했습니다: ${eventPayload?.mode || "AUTO"}`,
    rbac: "역할 권한을 확인하고 있습니다.",
    retrieval: "관련 데이터 컨텍스트를 검색하고 있습니다.",
    sql_generation: "조회 SQL을 생성하고 있습니다.",
    sql_validation: "생성 SQL을 검증하고 있습니다.",
    sql_execution: "Databricks에서 SQL을 실행하고 있습니다.",
    post_check: "응답 전 권한 검사를 수행하고 있습니다.",
    summarization: "조회 결과를 답변으로 정리하고 있습니다.",
    audit: "감사 로그를 기록하고 있습니다.",
    error: eventPayload?.detail || "스트리밍 처리 중 오류가 발생했습니다."
  };
  return labels[eventName] || "요청을 처리하고 있습니다.";
}

async function loadPublicRoles() {
  const response = await fetch("/api/admin/roles");
  publicRoles = await response.json();
  const roleSelect = document.getElementById("publicRoleSelect");
  roleSelect.innerHTML = publicRoles.map((role) => `
    <option value="${escapeHtml(role.role_id)}">${escapeHtml(role.role_id)}</option>
  `).join("");

  if (publicRoles.some((role) => role.role_id === "GENERAL_EMPLOYEE")) {
    roleSelect.value = "GENERAL_EMPLOYEE";
  }

  const selectedRole = getSelectedPublicRole();
  updatePublicIdentity({
    role_id: selectedRole.role_id,
    department_name: selectedRole.department,
    security_clearance: selectedRole.default_clearance
  });
  resetPublicChat();
}

function getSelectedPublicRole() {
  const roleId = document.getElementById("publicRoleSelect").value || "GENERAL_EMPLOYEE";
  return publicRoles.find((role) => role.role_id === roleId) || {
    role_id: roleId,
    role_name: roleId,
    department: "General",
    default_clearance: "INTERNAL"
  };
}

function updatePublicIdentity(identity) {
  const roleId = identity.role_id || "-";
  const department = identity.department_name || identity.department || "-";
  const clearance = identity.security_clearance || identity.default_clearance || "-";
  document.getElementById("publicRole").textContent = roleId;
  document.getElementById("publicDepartment").textContent = department;
  document.getElementById("publicClearance").textContent = clearance;
  document.getElementById("publicProfile").textContent = `${roleId} / ${clearance}`;
}

function resetPublicChat() {
  const selectedRole = getSelectedPublicRole();
  document.getElementById("publicMessages").innerHTML = `
    <div class="chat-message assistant">
      <span>Assistant</span>
      <p>궁금한 내용을 입력해 주세요. 선택한 Role에 맞춰 답변을 준비합니다.</p>
    </div>
  `;
  updatePublicIdentity({
    role_id: selectedRole.role_id,
    department_name: selectedRole.department,
    security_clearance: selectedRole.default_clearance
  });
  setPublicGuard("READY", false);
  renderPublicSources({});
}

function renderPublicSources(sources) {
  const tables = Array.isArray(sources.tables) ? sources.tables : [];
  const documents = Array.isArray(sources.documents) ? sources.documents : [];
  document.getElementById("publicCitations").innerHTML = renderSourceSections(tables, documents);
}

function renderSourceSections(tables, documents) {
  const sections = [];
  if (tables.length) {
    sections.push(`
      <div class="source-section">
        <strong>조회 table</strong>
        ${tables.map((table) => `<div>${escapeHtml(table)}</div>`).join("")}
      </div>
    `);
  }
  if (documents.length) {
    sections.push(`
      <div class="source-section">
        <strong>문서 citation</strong>
        ${documents.map((doc) => `<div>${formatDocument(doc)}</div>`).join("")}
      </div>
    `);
  }
  return sections.length ? sections.join("") : '<span class="muted-empty">반환된 출처 없음</span>';
}

function getResultKind(data) {
  const guardStatus = String(data.guard_status || "").toUpperCase();
  const preCheck = String(data.checks?.pre_check || "").toUpperCase();
  const postCheck = String(data.checks?.post_check || "").toUpperCase();
  if (guardStatus === "ERROR" || preCheck === "ERROR" || postCheck === "ERROR") {
    return {kind: "error", status: "ERROR"};
  }
  if (Boolean(data.blocked)
    || ["BLOCKED", "DENIED"].includes(guardStatus)
    || preCheck === "BLOCKED"
    || postCheck === "BLOCKED") {
    return {kind: "blocked", status: "BLOCKED"};
  }
  return {kind: "success", status: guardStatus || "PASS"};
}

function setPublicGuard(status, blocked) {
  const normalized = String(status || "UNKNOWN").toUpperCase();
  document.getElementById("publicGuard").textContent = normalized;
  const guardCard = document.getElementById("publicGuard").closest(".guard-card");
  guardCard.classList.toggle("blocked", blocked);
  guardCard.classList.toggle("error", ["ERROR", "FAILED", "FAILURE"].includes(normalized));
  guardCard.classList.toggle("running", ["RUNNING", "PENDING", "WAITING"].includes(normalized));
}

function appendBlockingMessage(data) {
  const checks = data.checks || {};
  const role = data.effective_identity?.role_id || data.role_id || "현재 사용자";
  const reason = data.answer || "현재 권한으로는 해당 질문에 관련된 데이터에 접근할 수 없습니다.";
  const detail = [
    `Role: ${role}`,
    `Pre-check: ${checks.pre_check || "UNKNOWN"}`,
    `Post-check: ${checks.post_check || "UNKNOWN"}`
  ].join(" · ");

  const message = document.createElement("div");
  message.className = "chat-message assistant blocked";
  message.innerHTML = `
    <div class="message-meta">
      <span>Access blocked</span>
      <strong class="status-pill blocked">BLOCKED</strong>
    </div>
    <div class="blocking-message">
      <strong>권한 확인 결과, 답변이 차단되었습니다.</strong>
      <p>${escapeHtml(reason)}</p>
      <small>${escapeHtml(detail)}</small>
    </div>
  `;
  appendMessageElement(message);
}

function appendErrorMessage(data) {
  const checks = data.checks || {};
  const role = data.effective_identity?.role_id || data.role_id || "현재 사용자";
  const reason = data.answer || "답변 생성 중 오류가 발생했습니다.";
  const detail = [
    `Role: ${role}`,
    `Pre-check: ${checks.pre_check || "ERROR"}`,
    `Post-check: ${checks.post_check || "ERROR"}`
  ].join(" · ");

  const message = document.createElement("div");
  message.className = "chat-message assistant error";
  message.innerHTML = `
    <div class="message-meta">
      <span>System error</span>
      <strong class="status-pill error">ERROR</strong>
    </div>
    <div class="blocking-message">
      <strong>실행 오류로 답변을 가져오지 못했습니다.</strong>
      <p>${escapeHtml(reason)}</p>
      <small>${escapeHtml(detail)}</small>
    </div>
  `;
  appendMessageElement(message);
}

function appendPublicMessage(type, label, text, meta = null) {
  const message = document.createElement("div");
  message.className = `chat-message ${type}`;
  const body = type.includes("markdown")
    ? `<div class="answer-body">${renderAnswer(text)}</div>`
    : `<p>${escapeHtml(text)}</p>`;
  const metaHtml = meta
    ? `
      <div class="message-meta">
        <span>${escapeHtml(label)}</span>
        <strong class="status-pill success">${escapeHtml(meta.status || "PASS")}</strong>
        <small>${escapeHtml([meta.role, meta.clearance].filter(Boolean).join(" / "))}</small>
      </div>
    `
    : `<span>${escapeHtml(label)}</span>`;
  message.innerHTML = `${metaHtml}${body}`;
  appendMessageElement(message);
}

function appendMessageElement(message) {
  const messages = document.getElementById("publicMessages");
  messages.appendChild(message);
  message.scrollIntoView({block: "nearest", behavior: "smooth"});
}

function renderAnswer(value) {
  const lines = String(value).split(/\r?\n/);
  const html = [];
  let paragraph = [];

  const flushParagraph = () => {
    if (!paragraph.length) {
      return;
    }
    html.push(`<p>${formatInline(paragraph.join("\n"))}</p>`);
    paragraph = [];
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();

    if (!line || line === "---") {
      flushParagraph();
      continue;
    }

    if (isMarkdownTableStart(lines, index)) {
      flushParagraph();
      const tableLines = [];
      while (index < lines.length && isMarkdownTableLine(lines[index])) {
        tableLines.push(lines[index]);
        index += 1;
      }
      index -= 1;
      html.push(renderMarkdownTable(tableLines));
      continue;
    }

    if (line.startsWith("### ")) {
      flushParagraph();
      html.push(`<h4>${formatInline(line.slice(4).replace(/:$/, ""))}</h4>`);
      continue;
    }

    if (line.startsWith("> ")) {
      flushParagraph();
      html.push(`<blockquote>${formatInline(line.slice(2))}</blockquote>`);
      continue;
    }

    if (line.startsWith("- ")) {
      flushParagraph();
      const items = [];
      while (index < lines.length && lines[index].trim().startsWith("- ")) {
        items.push(`<li>${formatInline(lines[index].trim().slice(2))}</li>`);
        index += 1;
      }
      index -= 1;
      html.push(`<ul>${items.join("")}</ul>`);
      continue;
    }

    paragraph.push(line);
  }

  flushParagraph();
  return html.join("");
}

function isMarkdownTableLine(line) {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|");
}

function isMarkdownTableStart(lines, index) {
  return isMarkdownTableLine(lines[index] || "") && isMarkdownTableLine(lines[index + 1] || "");
}

function renderMarkdownTable(tableLines) {
  const rows = tableLines
    .filter((line) => !/^\|\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|$/.test(line.trim()))
    .map((line) => line.trim().slice(1, -1).split("|").map((cell) => cell.trim()));

  if (!rows.length) {
    return "";
  }

  const headers = rows[0];
  const bodyRows = rows.slice(1);
  return `
    <div class="answer-table-wrap">
      <table class="answer-table">
        <thead><tr>${headers.map((cell) => `<th>${formatInline(cell)}</th>`).join("")}</tr></thead>
        <tbody>
          ${bodyRows.map((row) => `<tr>${row.map((cell) => `<td>${formatInline(cell)}</td>`).join("")}</tr>`).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function formatDocument(doc) {
  if (typeof doc === "string") {
    return escapeHtml(doc);
  }
  const parts = [doc.document_id, doc.chunk_id, doc.classification].filter(Boolean);
  return escapeHtml(parts.join(" / "));
}

function formatInline(value) {
  return escapeHtml(value)
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

loadPublicRoles();
