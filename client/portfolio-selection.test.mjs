import { test } from "node:test";
import assert from "node:assert/strict";
import { autoSelect, selectionAfterCancel } from "./src/portfolio-selection.js";

const list = [{ name: "alpha" }, { name: "beta" }];

test("autoSelect picks the first portfolio only when idle and nothing is selected", () => {
  assert.equal(autoSelect(list, null, false), "alpha");
  assert.equal(autoSelect(list, "beta", false), null);
  assert.equal(autoSelect([], null, false), null);
});

test("autoSelect never fires while a new portfolio is being created", () => {
  assert.equal(autoSelect(list, null, true), null);
});

test("selectionAfterCancel returns the previous selection, else the first one", () => {
  assert.equal(selectionAfterCancel(list, "beta"), "beta");
  assert.equal(selectionAfterCancel(list, "gone"), "alpha");
  assert.equal(selectionAfterCancel(list, null), "alpha");
  assert.equal(selectionAfterCancel([], null), null);
});
