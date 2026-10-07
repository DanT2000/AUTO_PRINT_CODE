// Порт совпадает с Python-версией: разбиение на единицы печати, удаление комментариев, выделение.
// Эталон — tests/fixtures/python-reference.json, получен из autoprint/*.py на тех же текстах.
// Python считает позиции в кодовых точках, JS — в UTF-16, поэтому эталонные позиции переводятся.
import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { stripComments } from "../shared/comments.ts";
import { DEFAULT_SETTINGS, typingSlice } from "../shared/model.ts";
import { buildUnits } from "../server/units.ts";

const ref = JSON.parse(fs.readFileSync(new URL("./fixtures/python-reference.json", import.meta.url), "utf-8"));

/** кодовая точка → индекс UTF-16 */
function cpToUtf16(text: string): (cp: number) => number {
  const offs = [0];
  for (const ch of text) offs.push(offs[offs.length - 1] + ch.length);
  return (cp) => offs[cp];
}

test("buildUnits = build_units", () => {
  for (const c of ref.units) {
    const conv = cpToUtf16(c.text);
    const got = buildUnits(c.text, { ...DEFAULT_SETTINGS, ...c.settings }).map((u) => [u.kind, u.text, u.srcEnd]);
    const exp = c.units.map(([k, t, e]: [string, string, number]) => [k, t, conv(e)]);
    assert.deepEqual(got, exp, JSON.stringify(c.settings));
  }
});

test("stripComments = strip_comments", () => {
  for (const c of ref.strip) {
    const conv = cpToUtf16(c.text);
    const [out, map] = stripComments(c.text, c.lang);
    assert.equal(out, c.out);
    // для символов вне BMP JS-карта даёт по индексу на каждую половину суррогатной пары — сравниваем по символам
    const firstOfEach = [...out].map((_, i) => map[[...out].slice(0, i).join("").length]);
    assert.deepEqual(firstOfEach, c.map.map(conv));
  }
});

test("typingSlice = typing_slice", () => {
  for (const c of ref.slices) assert.deepEqual(typingSlice(c.text, c.sel, c.whole), c.out);
});

test("имитация человека не меняет итоговый текст", () => {
  const text = ref.units[0].text as string;
  for (let seed = 1; seed <= 30; seed++) {
    let x = seed;
    const rng = () => ((x = (x * 1103515245 + 12345) % 2147483648) / 2147483648);
    const units = buildUnits(text, { ...DEFAULT_SETTINGS, profile: "plain", human_typing: true, typo_per_100_words: 30 }, rng);
    let out = "";
    for (const u of units) {
      if (u.kind === "back") out = out.slice(0, -1);
      else if (u.kind !== "cleanup") out += u.text;
    }
    assert.equal(out, text.replace(/\t/g, "    "), `seed ${seed}`);
    assert.ok(units.some((u) => u.kind === "back"), "должны быть опечатки");
  }
});
