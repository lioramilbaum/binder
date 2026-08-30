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

const dateFormatter = new Intl.DateTimeFormat("he-IL", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

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
    const item = document.createElement("article");
    item.className = "document-item";
    const title = document.createElement("h3");
    title.textContent = documentItem.title;
    const updated = document.createElement("span");
    updated.textContent = `עודכן ${dateFormatter.format(new Date(documentItem.updated_at))}`;
    item.append(title, updated);
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

dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
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
