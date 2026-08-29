const foldersElement = document.querySelector("#folders");
const emptyState = document.querySelector("#empty-state");
const folderCount = document.querySelector("#folder-count");
const dialog = document.querySelector("#folder-dialog");
const form = document.querySelector("#folder-form");
const formError = document.querySelector("#form-error");

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
    card.addEventListener("click", () => {
      document.querySelectorAll(".folder-card").forEach((item) => item.classList.remove("selected"));
      card.classList.add("selected");
      card.querySelector(".updated").textContent = "התיקייה נבחרה לעבודה";
    });
    foldersElement.append(card);
  }
}

async function loadFolders() {
  const response = await fetch("/api/folders");
  renderFolders(await response.json());
}

document.querySelector("#new-folder-button").addEventListener("click", () => dialog.showModal());
document.querySelector("#close-dialog").addEventListener("click", () => dialog.close());
document.querySelector("#cancel-dialog").addEventListener("click", () => dialog.close());

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

loadFolders().catch(() => {
  foldersElement.textContent = "לא הצלחנו לטעון את התיקיות. נסו לרענן את העמוד.";
});
