/** A small browser demo. No build tools or network requests. */
const button = document.querySelector("#launch");
const status = document.querySelector("#status");
let launches = 0;

button.addEventListener("click", () => {
  launches += 1;
  status.textContent = `Idea launched ${launches} ${launches === 1 ? "time" : "times"}.`;
});
