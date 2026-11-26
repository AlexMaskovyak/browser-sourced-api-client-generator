async function loadMessage() {
  const subject = document.querySelector("#subject");
  try {
    const response = await fetch("/message?message_id=m1");
    if (!response.ok) throw new Error("Message unavailable");
    const message = await response.json();
    subject.textContent = message.subject;
  } catch {
    subject.textContent = "Message unavailable";
  }
}

loadMessage();
