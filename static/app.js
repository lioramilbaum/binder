const foldersElement = document.querySelector("#folders");
const emptyState = document.querySelector("#empty-state");
const folderCount = document.querySelector("#folder-count");
const dialog = document.querySelector("#folder-dialog");
const form = document.querySelector("#folder-form");
const formError = document.querySelector("#form-error");
const homeView = document.querySelector("#home-view");
const folderView = document.querySelector("#folder-view");
const documentsElement = document.querySelector("#documents");
const documentsEmptyState = document.querySelector("#documents-empty-state");
const documentDialog = document.querySelector("#document-dialog");
const documentForm = document.querySelector("#document-form");
const documentFormError = document.querySelector("#document-form-error");
let currentFolderId = null;
let currentDocumentId = null;

const dateFormatter = new Intl.DateTimeFormat("he-IL", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

function setupFileDropZone(inputId, zoneId, listId, pasteTargetId) {
  const input = document.querySelector(`#${inputId}`);
  const zone = document.querySelector(`#${zoneId}`);
  const list = document.querySelector(`#${listId}`);
  const pasteTarget = document.querySelector(`#${pasteTargetId}`);

  function renderSelectedFiles() {
    list.replaceChildren();
    for (const file of input.files) {
      const item = document.createElement("span");
      item.textContent = `${file.name} (${formatFileSize(file.size)})`;
      list.append(item);
    }
  }

  function addFiles(files) {
    const transfer = new DataTransfer();
    for (const file of input.files) transfer.items.add(file);
    for (const file of files) transfer.items.add(file);
    input.files = transfer.files;
    renderSelectedFiles();
  }

  zone.addEventListener("click", (event) => {
    if (event.target !== input && !event.target.closest("button")) input.click();
  });
  zone.addEventListener("keydown", (event) => {
    if (event.target.closest("button")) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      input.click();
    }
  });
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("dragging");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dragging");
    addFiles(event.dataTransfer.files);
  });
  input.addEventListener("change", renderSelectedFiles);
  pasteTarget.addEventListener("paste", (event) => {
    const files = [...event.clipboardData.files];
    if (files.length) {
      event.preventDefault();
      addFiles(files);
    }
  });

  return {
    addFiles,
    clear: () => {
      input.value = "";
      renderSelectedFiles();
    },
  };
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function uploadFiles(documentId, files) {
  if (!files.length) return;
  const uploadData = new FormData();
  files.forEach((file) => uploadData.append("files", file));
  const response = await fetch(
    `/api/folders/${encodeURIComponent(currentFolderId)}/documents/${encodeURIComponent(documentId)}/files`,
    { method: "POST", body: uploadData },
  );
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "העלאת הקבצים נכשלה.");
}

const documentFiles = setupFileDropZone(
  "document-files",
  "document-file-drop-zone",
  "document-selected-files",
  "document-content",
);

const googleDriveImportButton = document.querySelector("#google-drive-import-button");
let googleConfig = { googleClientId: "", googleApiKey: "" };
let googleApisReady = null;
let googleTokenClient = null;
let googleAccessToken = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`טעינת ${src} נכשלה.`));
    document.head.append(script);
  });
}

function ensureGoogleApisReady() {
  if (!googleApisReady) {
    googleApisReady = Promise.all([
      loadScript("https://accounts.google.com/gsi/client"),
      loadScript("https://apis.google.com/js/api.js").then(
        () => new Promise((resolve) => gapi.load("picker", resolve)),
      ),
    ]);
  }
  return googleApisReady;
}

function requestGoogleAccessToken() {
  return new Promise((resolve, reject) => {
    if (!googleTokenClient) {
      googleTokenClient = google.accounts.oauth2.initTokenClient({
        client_id: googleConfig.googleClientId,
        scope: "https://www.googleapis.com/auth/drive.readonly",
        callback: () => {},
      });
    }
    googleTokenClient.callback = (response) => {
      if (response.error) {
        reject(new Error("ההתחברות ל-Google Drive נכשלה."));
        return;
      }
      googleAccessToken = response.access_token;
      resolve(googleAccessToken);
    };
    googleTokenClient.requestAccessToken({ prompt: googleAccessToken ? "" : "consent" });
  });
}

function openGoogleDrivePicker() {
  return new Promise((resolve, reject) => {
    const picker = new google.picker.PickerBuilder()
      .addView(google.picker.ViewId.DOCS)
      .setOAuthToken(googleAccessToken)
      .setDeveloperKey(googleConfig.googleApiKey)
      .setCallback((data) => {
        if (data.action === google.picker.Action.PICKED) resolve(data.docs);
        else if (data.action === google.picker.Action.CANCEL) resolve([]);
      })
      .build();
    picker.setVisible(true);
  });
}

async function downloadGoogleDriveFile(doc) {
  const isGoogleNative = doc.mimeType.startsWith("application/vnd.google-apps");
  const url = isGoogleNative
    ? `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(doc.id)}/export?mimeType=${encodeURIComponent("application/pdf")}`
    : `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(doc.id)}?alt=media`;
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${googleAccessToken}` },
  });
  if (!response.ok) throw new Error(`הורדת "${doc.name}" מ-Google Drive נכשלה.`);
  const blob = await response.blob();
  const name = isGoogleNative ? `${doc.name}.pdf` : doc.name;
  const type = isGoogleNative ? "application/pdf" : doc.mimeType || blob.type;
  return new File([blob], name, { type });
}

googleDriveImportButton?.addEventListener("click", async () => {
  const originalText = googleDriveImportButton.textContent;
  googleDriveImportButton.disabled = true;
  try {
    await ensureGoogleApisReady();
    await requestGoogleAccessToken();
    const docs = await openGoogleDrivePicker();
    if (!docs.length) return;
    googleDriveImportButton.textContent = "מוריד קבצים…";
    const files = [];
    for (const doc of docs) files.push(await downloadGoogleDriveFile(doc));
    documentFiles.addFiles(files);
  } catch (error) {
    documentFormError.textContent = error.message || "יבוא מ-Google Drive נכשל.";
  } finally {
    googleDriveImportButton.disabled = false;
    googleDriveImportButton.textContent = originalText;
  }
});

async function loadGoogleConfig() {
  try {
    const response = await fetch("/api/config");
    googleConfig = await response.json();
  } catch {
    return;
  }
  if (googleDriveImportButton) {
    googleDriveImportButton.hidden = !(googleConfig.googleClientId && googleConfig.googleApiKey);
  }
}

function renderFolders(folders) {
  foldersElement.replaceChildren();
  emptyState.hidden = folders.length !== 0;
  folderCount.textContent = folders.length ? `${folders.length} תיקיות` : "";

  for (const folder of folders) {
    const card = document.createElement("button");
    card.className = "folder-card";
    card.type = "button";
    card.dataset.folderId = folder.id;

    const icon = document.createElement("span");
    icon.className = "folder-icon";
    icon.textContent = "⌑";
    icon.setAttribute("aria-hidden", "true");

    const name = document.createElement("strong");
    name.textContent = folder.name;

    const description = document.createElement("span");
    description.className = "description";
    description.textContent = folder.description || "ללא תיאור";

    const updated = document.createElement("span");
    updated.className = "updated";
    updated.textContent = `עודכן ${dateFormatter.format(new Date(folder.updated_at))}`;

    card.append(icon, name, description, updated);
    card.addEventListener("click", () => openFolder(folder.id));
    foldersElement.append(card);
  }
}

const documentDialogEyebrow = document.querySelector("#document-dialog-eyebrow");
const documentDialogTitle = document.querySelector("#document-dialog-title");
const documentSubmitButton = documentForm.querySelector('button[type="submit"]');

function renderDocuments(documents) {
  const openIds = new Set(
    [...documentsElement.querySelectorAll("details.document-item[open]")].map(
      (details) => details.dataset.documentId,
    ),
  );
  documentsElement.replaceChildren();
  documentsEmptyState.hidden = documents.length !== 0;
  document.querySelector("#document-count").textContent =
    documents.length ? `${documents.length} מסמכים` : "";

  for (const documentItem of documents) {
    const item = document.createElement("details");
    item.className = "document-item";
    item.dataset.documentId = documentItem.id;
    if (openIds.has(documentItem.id)) item.open = true;
    const summary = document.createElement("summary");
    const summaryText = document.createElement("span");
    const title = document.createElement("h3");
    title.textContent = documentItem.name;
    const documentDate = document.createElement("span");
    const formattedDate = dateFormatter.format(new Date(`${documentItem.date}T00:00:00`));
    documentDate.textContent = documentItem.time
      ? `${formattedDate}, ${documentItem.time}`
      : formattedDate;
    summaryText.append(title, documentDate);
    const marker = document.createElement("span");
    marker.className = "document-marker";
    marker.textContent = "⌄";
    summary.append(summaryText, marker);
    const body = document.createElement("div");
    body.className = "document-body";

    const editActions = document.createElement("div");
    editActions.className = "document-actions";

    const editButton = document.createElement("button");
    editButton.className = "secondary-button small-button";
    editButton.type = "button";
    editButton.textContent = "עריכת מסמך";
    editButton.addEventListener("click", () => {
      currentDocumentId = documentItem.id;
      documentDialogEyebrow.textContent = "עריכת מסמך";
      documentDialogTitle.textContent = "עדכון פרטי המסמך";
      documentSubmitButton.textContent = "שמירת שינויים";

      document.querySelector("#document-date").value = documentItem.date;
      document.querySelector("#document-time").value = documentItem.time || "";
      document.querySelector("#document-name").value = documentItem.name;
      document.querySelector("#document-description").value = documentItem.description || "";
      document.querySelector("#document-content").value = documentItem.content || "";

      documentFormError.textContent = "";
      documentFiles.clear();
      documentDialog.showModal();
    });

    editActions.append(editButton);

    if (documentItem.whatsapp_chat?.messages?.length) {
      const chatToggle = document.createElement("button");
      chatToggle.className = "secondary-button small-button";
      chatToggle.type = "button";
      chatToggle.textContent = "הצגת שיחת WhatsApp";
      let chatElement = null;
      chatToggle.addEventListener("click", () => {
        if (chatElement) {
          chatElement.remove();
          chatElement = null;
          chatToggle.textContent = "הצגת שיחת WhatsApp";
        } else {
          chatElement = renderWhatsAppChat(documentItem.whatsapp_chat);
          body.append(chatElement);
          chatToggle.textContent = "הסתרת שיחת WhatsApp";
        }
      });
      editActions.append(chatToggle);
    }

    body.append(editActions);

    if (documentItem.description) {
      const description = document.createElement("p");
      description.className = "document-description";
      description.textContent = documentItem.description;
      body.append(description);
    }
    if (documentItem.files?.length) {
      const files = document.createElement("div");
      files.className = "attachment-grid";
      for (const attachment of documentItem.files) {
        const url = `/api/folders/${encodeURIComponent(currentFolderId)}/documents/${encodeURIComponent(documentItem.id)}/files/${encodeURIComponent(attachment.id)}`;
        const item = document.createElement("div");
        item.className = "attachment-item";
        const link = document.createElement("a");
        link.className = "attachment";
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener";
        if (attachment.content_type.startsWith("image/")) {
          const image = document.createElement("img");
          image.src = url;
          image.alt = attachment.name;
          link.append(image);
        }
        const filename = document.createElement("span");
        filename.textContent = attachment.name;
        link.append(filename);
        const removeButton = document.createElement("button");
        removeButton.className = "attachment-remove";
        removeButton.type = "button";
        removeButton.textContent = "×";
        removeButton.title = "הסרת קובץ";
        removeButton.setAttribute("aria-label", "הסרת קובץ");
        removeButton.addEventListener("click", async () => {
          if (!confirm(`למחוק את הקובץ "${attachment.name}"?`)) return;
          removeButton.disabled = true;
          const response = await fetch(url, { method: "DELETE" });
          if (!response.ok) {
            removeButton.disabled = false;
            alert("מחיקת הקובץ נכשלה.");
            return;
          }
          await openFolder(currentFolderId, false);
        });
        item.append(link, removeButton);
        files.append(item);
      }
      body.append(files);
    }
    item.append(summary, body);
    documentsElement.append(item);
  }
}

function renderWhatsAppChat(chat) {
  const wrapper = document.createElement("div");
  const conversation = document.createElement("div");
  conversation.className = "whatsapp-chat";
  let previousDate = null;

  for (const message of chat.messages) {
    if (message.date !== previousDate) {
      const dateSeparator = document.createElement("div");
      dateSeparator.className = "chat-date";
      dateSeparator.textContent = message.date;
      conversation.append(dateSeparator);
      previousDate = message.date;
    }
    if (message.system) {
      const system = document.createElement("div");
      system.className = "chat-system-message";
      system.textContent = message.content;
      conversation.append(system);
      continue;
    }
    const row = document.createElement("div");
    const ownerName = chat.me || "Liora Milbaum";
    row.className = `chat-message-row ${message.sender === ownerName ? "mine" : "theirs"}`;
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    const sender = document.createElement("strong");
    sender.textContent = message.sender;
    bubble.append(sender);
    if (message.reply_to) {
      const quote = document.createElement("div");
      quote.className = "chat-reply-quote";
      const quoteSender = document.createElement("strong");
      quoteSender.textContent = message.reply_to.sender;
      const quoteText = document.createElement("span");
      quoteText.textContent = message.reply_to.content;
      quote.append(quoteSender, quoteText);
      bubble.append(quote);
    }
    const content = document.createElement("p");
    appendLinkifiedText(content, message.content);
    const time = document.createElement("time");
    time.textContent = message.time;
    bubble.append(content, time);
    row.append(bubble);
    conversation.append(row);
  }
  wrapper.append(conversation);
  return wrapper;
}

function appendLinkifiedText(element, text) {
  const linkPattern = /(?:https?:\/\/|www\.)[^\s<>]+/gi;
  let cursor = 0;
  for (const match of text.matchAll(linkPattern)) {
    element.append(document.createTextNode(text.slice(cursor, match.index)));
    const trailingMatch = match[0].match(/[.,!?;:)}\]]+$/);
    const trailing = trailingMatch ? trailingMatch[0] : "";
    const linkText = trailing ? match[0].slice(0, -trailing.length) : match[0];
    const link = document.createElement("a");
    link.href = linkText.startsWith("www.") ? `https://${linkText}` : linkText;
    link.textContent = linkText;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    element.append(link);
    if (trailing) element.append(document.createTextNode(trailing));
    cursor = match.index + match[0].length;
  }
  element.append(document.createTextNode(text.slice(cursor)));
}

async function openFolder(folderId, updateHash = true) {
  const response = await fetch(`/api/folders/${encodeURIComponent(folderId)}`);
  if (!response.ok) {
    window.location.hash = "";
    throw new Error("Folder not found");
  }
  const folder = await response.json();
  currentFolderId = folder.id;
  document.querySelector("#current-folder-name").textContent = folder.name;
  document.querySelector("#current-folder-description").textContent =
    folder.description || "ללא תיאור";
  renderDocuments(folder.documents || []);
  homeView.hidden = true;
  folderView.hidden = false;
  document.title = `${folder.name} — Binder`;
  if (updateHash) history.pushState(null, "", `#folder/${folder.id}`);
}

function showHome(updateHash = true) {
  currentFolderId = null;
  folderView.hidden = true;
  homeView.hidden = false;
  document.title = "Binder — ניהול מידע";
  if (updateHash) history.pushState(null, "", window.location.pathname);
}

async function loadFolders() {
  const response = await fetch("/api/folders");
  renderFolders(await response.json());
}

document.querySelector("#new-folder-button").addEventListener("click", () => dialog.showModal());
document.querySelector("#close-dialog").addEventListener("click", () => dialog.close());
document.querySelector("#cancel-dialog").addEventListener("click", () => dialog.close());
document.querySelector("#back-button").addEventListener("click", () => showHome());
document.querySelector("#new-document-button").addEventListener("click", () => {
  currentDocumentId = null;
  documentDialogEyebrow.textContent = "מסמך חדש";
  documentDialogTitle.textContent = "הוספת מידע לתיקייה";
  documentSubmitButton.textContent = "שמירת מסמך";
  documentForm.reset();
  documentFiles.clear();
  documentFormError.textContent = "";
  document.querySelector("#document-date").value = new Date().toISOString().slice(0, 10);
  documentDialog.showModal();
});
document.querySelector("#close-document-dialog").addEventListener("click", () => documentDialog.close());
document.querySelector("#cancel-document-dialog").addEventListener("click", () => documentDialog.close());

dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});

documentDialog.addEventListener("click", (event) => {
  if (event.target === documentDialog) documentDialog.close();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.textContent = "";
  const data = new FormData(form);
  const response = await fetch("/api/folders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: data.get("name"), description: data.get("description") }),
  });
  const result = await response.json();
  if (!response.ok) {
    formError.textContent = result.error;
    return;
  }
  form.reset();
  dialog.close();
  await loadFolders();
});

documentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  documentFormError.textContent = "";
  const submitButton = documentForm.querySelector('button[type="submit"]');
  const originalButtonText = submitButton.textContent;
  submitButton.disabled = true;
  submitButton.textContent = "שומר…";
  const data = new FormData(documentForm);
  try {
    const isEdit = !!currentDocumentId;
    const url = isEdit
      ? `/api/folders/${encodeURIComponent(currentFolderId)}/documents/${encodeURIComponent(currentDocumentId)}`
      : `/api/folders/${encodeURIComponent(currentFolderId)}/documents`;
    const method = isEdit ? "PATCH" : "POST";

    const response = await fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        date: data.get("date"),
        time: data.get("time"),
        name: data.get("name"),
        description: data.get("description"),
        content: data.get("content"),
      }),
    });
    const contentType = response.headers.get("Content-Type") || "";
    const result = contentType.includes("application/json")
      ? await response.json()
      : null;
    if (!response.ok) {
      documentFormError.textContent =
        result?.error || "השמירה נכשלה. יש להפעיל מחדש את השרת ולנסות שוב.";
      return;
    }
    const selectedFiles = data.getAll("files").filter((file) => file.size > 0);
    // Retry an upload against the saved document instead of creating a duplicate.
    currentDocumentId = result.id;
    await uploadFiles(result.id, selectedFiles);
    documentForm.reset();
    documentFiles.clear();
    documentDialog.close();
    await openFolder(currentFolderId, false);
  } catch (error) {
    documentFormError.textContent =
      error.message || "לא ניתן להתחבר לשרת. ודאו שהשרת פועל ונסו שוב.";
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = originalButtonText;
  }
});

async function start() {
  await loadFolders();
  loadGoogleConfig();
  const folderMatch = window.location.hash.match(/^#folder\/(.+)$/);
  if (folderMatch) await openFolder(folderMatch[1], false);
}

window.addEventListener("hashchange", () => {
  const folderMatch = window.location.hash.match(/^#folder\/(.+)$/);
  if (folderMatch) openFolder(folderMatch[1], false);
  else showHome(false);
});

start().catch(() => {
  foldersElement.textContent = "לא הצלחנו לטעון את התיקיות. נסו לרענן את העמוד.";
});
