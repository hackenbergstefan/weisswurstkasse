if (window.lucide) window.lucide.createIcons();

const currency = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "EUR",
});

document.querySelectorAll(".order-form").forEach((form) => {
  const inputs = [...form.querySelectorAll("input[data-price]")];
  const receiptLines = [...form.querySelectorAll("[data-receipt-for]")];
  const quantity = (input) => Math.min(100, Math.max(0, Number(input.value) || 0));
  const update = () => {
    const total = inputs.reduce(
      (sum, input) =>
        sum +
        quantity(input) * Number(input.dataset.price),
      Number(form.dataset.fixedTotal),
    );
    const output = form.querySelector(".order-total");
    if (output) output.textContent = currency.format(total / 100);
    inputs.forEach((input) => {
      const amount = quantity(input);
      const line = receiptLines.find((item) => item.dataset.receiptFor === input.id);
      if (line) {
        line.hidden = amount === 0;
        line.querySelector("[data-receipt-quantity]").textContent = amount;
        line.querySelector("[data-receipt-total]").textContent = currency.format(
          amount * Number(input.dataset.price) / 100,
        );
      }
      const control = input.closest(".quantity-control");
      control.querySelector('[data-step="-1"]').disabled = amount === 0;
      control.querySelector('[data-step="1"]').disabled = amount === 100;
    });
  };
  inputs.forEach((input) => input.addEventListener("input", update));
  form.querySelectorAll("[data-step]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = button.closest(".quantity-control").querySelector("input");
      input.value = Math.min(
        100,
        Math.max(0, (Number(input.value) || 0) + Number(button.dataset.step)),
      );
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });
  update();
});

document.querySelectorAll("[data-amount]").forEach((button) => {
  button.addEventListener("click", () => {
    const form = button.closest("form");
    form.querySelector('input[name="paypal-amount"]').value = Number(
      button.dataset.amount,
    ).toFixed(2);
    form
      .querySelectorAll("[data-amount]")
      .forEach((option) =>
        option.setAttribute("aria-pressed", String(option === button)),
      );
  });
});

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", () => {
    form.querySelectorAll('button[type="submit"]').forEach((button) => {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    });
  });
});
window.addEventListener("pageshow", () =>
  document.querySelectorAll('[aria-busy="true"]').forEach((button) => {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }),
);
