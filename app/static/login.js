document.getElementById("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/admin/login", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      username: document.getElementById("username").value,
      password: document.getElementById("password").value
    })
  });
  const data = await response.json();
  const message = document.getElementById("loginMessage");
  message.textContent = data.message;

  if (data.ok) {
    sessionStorage.setItem("cosbelle_admin", "true");
    window.location.href = data.redirect;
  }
});
