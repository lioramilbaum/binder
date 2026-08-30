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
const contentDialog = document.querySelector("#content-dialog");
const contentForm = document.querySelector("#content-form");
const contentFormError = document.querySelector("#content-form-error");
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
    if (event.target !== input) input.click();
  });
  zone.addEventListener("keydown", (event) => {
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

  return () => {
    input.value = "";
    renderSelectedFiles();
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

const clearNewDocumentFiles = setupFileDropZone(
  "document-files",
  "document-file-drop-zone",
  "document-selected-files",
  "document-content",
);
const clearExistingDocumentFiles = setupFileDropZone(
  "existing-document-files",
  "existing-file-drop-zone",
  "existing-selected-files",
  "existing-document-content",
);

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

function renderDocuments(documents) {
  documentsElement.replaceChildren();
  documentsEmptyState.hidden = documents.length !== 0;
  document.querySelector("#document-count").textContent =
    documents.length ? `${documents.length} מסמכים` : "";

  for (const documentItem of documents) {
    const item = document.createElement("details");
    item.className = "document-item";
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
    if (documentItem.description) {
      const description = document.createElement("p");
      description.className = "document-description";
      description.textContent = documentItem.description;
      body.append(description);
    }
    const content = document.createElement("pre");
    content.textContent = documentItem.content || "אין תוכן במסמך.";
    body.append(content);
    const editButton = document.createElement("button");
    editButton.className = "secondary-button edit-content-button";
    editButton.type = "button";
    editButton.textContent = "עריכת תוכן";
    editButton.addEventListener("click", () => {
      currentDocumentId = documentItem.id;
      document.querySelector("#content-dialog-title").textContent = documentItem.name;
      document.querySelector("#existing-document-content").value = documentItem.content || "";
      contentFormError.textContent = "";
      clearExistingDocumentFiles();
      contentDialog.showModal();
    });
    body.append(editButton);
    if (documentItem.files?.length) {
      const files = document.createElement("div");
      files.className = "attachment-grid";
      for (const attachment of documentItem.files) {
        const url = `/api/folders/${encodeURIComponent(currentFolderId)}/documents/${encodeURIComponent(documentItem.id)}/files/${encodeURIComponent(attachment.id)}`;
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
        files.append(link);
      }
      body.append(files);
    }
    item.append(summary, body);
    documentsElement.append(item);
  }
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
  document.title = "Binder — התיקיות שלי";
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
  document.querySelector("#document-date").value = new Date().toISOString().slice(0, 10);
  documentDialog.showModal();
});
document.querySelector("#close-document-dialog").addEventListener("click", () => documentDialog.close());
document.querySelector("#cancel-document-dialog").addEventListener("click", () => documentDialog.close());
document.querySelector("#close-content-dialog").addEventListener("click", () => contentDialog.close());
document.querySelector("#cancel-content-dialog").addEventListener("click", () => contentDialog.close());

dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});

documentDialog.addEventListener("click", (event) => {
  if (event.target === documentDialog) documentDialog.close();
});

contentDialog.addEventListener("click", (event) => {
  if (event.target === contentDialog) contentDialog.close();
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
    const response = await fetch(`/api/folders/${encodeURIComponent(currentFolderId)}/documents`, {
      method: "POST",
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
    await uploadFiles(result.id, selectedFiles);
    documentForm.reset();
    clearNewDocumentFiles();
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

contentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  contentFormError.textContent = "";
  const submitButton = contentForm.querySelector('button[type="submit"]');
  const originalButtonText = submitButton.textContent;
  submitButton.disabled = true;
  submitButton.textContent = "שומר…";
  try {
    const response = await fetch(
      `/api/folders/${encodeURIComponent(currentFolderId)}/documents/${encodeURIComponent(currentDocumentId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: document.querySelector("#existing-document-content").value,
        }),
      },
    );
    const result = await response.json();
    if (!response.ok) {
      contentFormError.textContent = result.error || "שמירת התוכן נכשלה.";
      return;
    }
    const data = new FormData(contentForm);
    const selectedFiles = data.getAll("files").filter((file) => file.size > 0);
    await uploadFiles(currentDocumentId, selectedFiles);
    clearExistingDocumentFiles();
    contentDialog.close();
    await openFolder(currentFolderId, false);
  } catch (error) {
    contentFormError.textContent =
      error.message || "לא ניתן להתחבר לשרת. ודאו שהשרת פועל ונסו שוב.";
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = originalButtonText;
  }
});

async function start() {
  await loadFolders();
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
