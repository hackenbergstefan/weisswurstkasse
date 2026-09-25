const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(`${__dirname}/../static/app.js`, "utf8");
const money = (cents) => new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(cents / 100);

function makeForm(id, value, price, fixed = 0) {
  const handlers = {};
  const output = { textContent: "" };
  const receiptQuantity = { textContent: "" };
  const receiptTotal = { textContent: "" };
  const line = {
    dataset: { receiptFor: id },
    hidden: false,
    querySelector: (selector) => selector === "[data-receipt-quantity]" ? receiptQuantity : receiptTotal,
  };
  const buttons = [-1, 1].map((step) => ({
    dataset: { step: String(step) },
    disabled: false,
    addEventListener: (name, handler) => { handlers[`${step}:${name}`] = handler; },
    closest: () => control,
  }));
  const input = {
    id,
    value: String(value),
    dataset: { price: String(price) },
    addEventListener: (name, handler) => { handlers[name] = handler; },
    dispatchEvent: (event) => handlers[event.type](),
    closest: () => control,
  };
  const control = {
    querySelector: (selector) => selector === "input" ? input : buttons[selector.includes("-1") ? 0 : 1],
  };
  const form = {
    dataset: { fixedTotal: String(fixed) },
    querySelectorAll: (selector) => ({
      "input[data-price]": [input],
      "[data-receipt-for]": [line],
      "[data-step]": buttons,
    })[selector] || [],
    querySelector: (selector) => selector === ".order-total" ? output : null,
    addEventListener() {},
  };
  return { form, input, output, line, receiptQuantity, receiptTotal, buttons, handlers };
}

function run(...states) {
  vm.runInNewContext(source, {
    Intl,
    Event: class { constructor(type) { this.type = type; } },
    document: { querySelectorAll: (selector) => [".order-form", "form"].includes(selector) ? states.map((state) => state.form) : [] },
    window: { addEventListener() {} },
  });
}

test("receipt includes immutable inactive charges and live line totals", () => {
  const state = makeForm("first", 2, 160, 90);
  run(state);
  assert.equal(state.output.textContent, money(410));
  assert.equal(state.receiptTotal.textContent, money(320));
  assert.equal(state.receiptQuantity.textContent, 2);
  state.handlers["1:click"]();
  assert.equal(state.output.textContent, money(570));
  assert.equal(state.receiptTotal.textContent, money(480));
});

test("zero quantities hide the line and disable decrement", () => {
  const state = makeForm("first", 1, 90);
  run(state);
  state.handlers["-1:click"]();
  assert.equal(state.output.textContent, money(0));
  assert.equal(state.line.hidden, true);
  assert.equal(state.buttons[0].disabled, true);
  state.handlers["1:click"]();
  assert.equal(state.line.hidden, false);
  assert.equal(state.buttons[0].disabled, false);
});

test("quantity controls stop at 100 and handle invalid input", () => {
  const state = makeForm("first", 99, 220);
  run(state);
  state.handlers["1:click"]();
  state.handlers["1:click"]();
  assert.equal(Number(state.input.value), 100);
  assert.equal(state.buttons[1].disabled, true);
  assert.equal(state.output.textContent, money(22000));
  for (const value of ["", "invalid", "-8"]) {
    state.input.value = value;
    state.handlers.input();
    assert.equal(state.output.textContent, money(0));
  }
});

test("orders calculate independently even with the same product price", () => {
  const first = makeForm("first", 2, 160);
  const second = makeForm("second", 1, 160);
  run(first, second);
  first.handlers["1:click"]();
  assert.equal(first.output.textContent, money(480));
  assert.equal(second.output.textContent, money(160));
  assert.equal(second.receiptQuantity.textContent, 1);
});
