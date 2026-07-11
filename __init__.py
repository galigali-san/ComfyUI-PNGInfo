# -*- coding: utf-8 -*-
"""PNG Info (Readable) — 画像のメタデータをA1111のPNG Info風に読める形で出す。

- A1111形式(parametersテキスト)はそのまま表示+項目抽出
- ComfyUI形式(prompt JSON)はグラフを解析してダイジェストを生成
- positive/negative/seed/steps/cfg を型付きピンでも出すので、
  古い画像から今のワークフローに値を差し込める
"""

import hashlib
import json
import os
import re

import numpy as np
import torch
from PIL import Image, ImageOps

import folder_paths

# リンク先を辿るときに「それっぽい値」として拾う入力名(優先順)
_VALUE_KEYS = ("text", "populated_text", "string", "string_a", "value",
               "seed", "noise_seed", "int", "float")


def _resolve(nodes, v, prefer=(), depth=0):
    """入力値を返す。リンク([node_id, slot])なら遡って値らしきものを探す。

    prefer: 探している値と同じ意味の入力名(例: stepsを探すなら("steps",))。
    リンク先のノードに同名の入力があればそれを最優先する。
    """
    if not (isinstance(v, list) and len(v) == 2):
        return v
    if depth > 6:
        return None
    src = nodes.get(str(v[0]))
    if not src:
        return None

    def ok(r):
        # 空文字は「まだ見つかっていない」扱いで先を探す
        return r is not None and not (isinstance(r, str) and not r.strip())

    ins = src.get("inputs", {})
    for key in tuple(prefer) + _VALUE_KEYS:
        if key in ins:
            r = _resolve(nodes, ins[key], prefer, depth + 1)
            if ok(r):
                return r
    # スカラー入力が1つだけならそれ(ただし区切り文字などの短すぎる文字列は除く)
    scalars = [x for x in ins.values() if not isinstance(x, list)]
    if len(scalars) == 1 and not (isinstance(scalars[0], str)
                                  and len(scalars[0]) < 2):
        return scalars[0]
    # だめならリンク入力を順に辿る(スイッチ/文字列加工ノード対策)
    for x in ins.values():
        if isinstance(x, list):
            r = _resolve(nodes, x, prefer, depth + 1)
            if ok(r):
                return r
    return None


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _digest_comfy(prompt_json):
    """ComfyUIのAPI形式JSONから読めるダイジェストと主要値を作る。"""
    try:
        nodes = json.loads(prompt_json)
    except ValueError:
        return None
    if not isinstance(nodes, dict):
        return None
    nodes = {str(k): v for k, v in nodes.items() if isinstance(v, dict)}

    out = {"positive": None, "negative": None, "seed": None, "steps": None,
           "cfg": None, "models": [], "loras": [], "passes": [],
           "width": None, "height": None}

    for nid, nd in nodes.items():
        ins = nd.get("inputs", {})
        if "ckpt_name" in ins and not isinstance(ins["ckpt_name"], list):
            out["models"].append(str(ins["ckpt_name"]))
        if "lora_name" in ins and not isinstance(ins["lora_name"], list):
            sm = _to_float(_resolve(nodes, ins.get("strength_model"),
                                    ("strength_model",)))
            out["loras"].append("%s(%s)" % (
                ins["lora_name"],
                ("%.2f" % sm) if sm is not None else "?"))
        if nd.get("class_type") == "EmptyLatentImage":
            out["width"] = _to_int(_resolve(nodes, ins.get("width"),
                                            ("width",)))
            out["height"] = _to_int(_resolve(nodes, ins.get("height"),
                                             ("height",)))

    # サンプラーらしきノード = stepsとsampler_nameを持つ。
    # Saver/Parameters系はメタデータ記録用に同名入力を持つので除外する
    _NOT_SAMPLER = re.compile(r"save|saver|parameter|info", re.I)
    samplers = []
    for nid, nd in nodes.items():
        ins = nd.get("inputs", {})
        cls = nd.get("class_type", "?")
        if "steps" not in ins or "sampler_name" not in ins:
            continue
        if _NOT_SAMPLER.search(cls):
            continue
        p = {
            "id": nid, "class": cls,
            "seed": _to_int(_resolve(nodes,
                                     ins.get("seed", ins.get("noise_seed")),
                                     ("seed", "noise_seed"))),
            "steps": _to_int(_resolve(nodes, ins.get("steps"), ("steps",))),
            "cfg": _to_float(_resolve(nodes, ins.get("cfg"), ("cfg",))),
            "sampler": _resolve(nodes, ins.get("sampler_name"),
                                ("sampler_name", "sampler")),
            "scheduler": _resolve(nodes, ins.get("scheduler"),
                                  ("scheduler",)),
            "denoise": _to_float(_resolve(nodes, ins.get("denoise"),
                                          ("denoise",))),
        }
        for side in ("positive", "negative"):
            v = ins.get(side)
            p[side] = _resolve(nodes, v, ("text",)) if isinstance(v, list) \
                else None
            if p[side] is not None:
                p[side] = str(p[side])
        samplers.append(p)
    # denoise=1.0(本命パス)を先頭に
    samplers.sort(key=lambda p: (0 if (p["denoise"] or 1.0) >= 0.99 else 1,
                                 _to_int(p["id"]) or 0))
    out["passes"] = samplers
    if samplers:
        main = samplers[0]
        for k in ("positive", "negative", "seed", "steps", "cfg"):
            out[k] = main[k]
    return out


def _parse_a1111(params):
    """A1111のparametersテキストから主要値を抜く。"""
    out = {"positive": "", "negative": "", "seed": None, "steps": None,
           "cfg": None}
    m = re.search(r"\nNegative prompt:", params)
    if m:
        out["positive"] = params[:m.start()].strip()
        rest = params[m.end():]
    else:
        lines = params.split("\n")
        out["positive"] = lines[0].strip()
        rest = "\n".join(lines[1:])
    km = re.search(r"\n(?=Steps:)", rest)
    if km:
        out["negative"] = rest[:km.start()].strip()
    sm = re.search(r"Steps:\s*(\d+)", params)
    if sm:
        out["steps"] = int(sm.group(1))
    sm = re.search(r"Seed:\s*(\d+)", params)
    if sm:
        out["seed"] = int(sm.group(1))
    sm = re.search(r"CFG scale:\s*([\d.]+)", params)
    if sm:
        out["cfg"] = float(sm.group(1))
    return out


class PNGInfoReadable:
    @classmethod
    def INPUT_TYPES(cls):
        input_dir = folder_paths.get_input_directory()
        files = [f for f in os.listdir(input_dir)
                 if os.path.isfile(os.path.join(input_dir, f))]
        return {"required": {"image": (sorted(files), {"image_upload": True})}}

    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "STRING", "INT", "INT",
                    "FLOAT")
    RETURN_NAMES = ("image", "info", "positive", "negative", "seed", "steps",
                    "cfg")
    FUNCTION = "read"
    CATEGORY = "image"
    DESCRIPTION = (u"画像のメタデータをA1111のPNG Info風に読める形で出す。"
                   u"A1111形式(parameters)とComfyUI形式(埋め込みワークフロー)"
                   u"の両対応。positive/seed等は型付き出力なので、古い画像の"
                   u"値を今のワークフローにそのまま差し込める。infoは"
                   u"showAnything系ノードに繋ぐと見やすい。")

    def read(self, image):
        path = folder_paths.get_annotated_filepath(image)
        img = Image.open(path)
        meta = dict(img.info)

        pil = ImageOps.exif_transpose(img).convert("RGB")
        arr = np.array(pil).astype(np.float32) / 255.0
        tensor = torch.from_numpy(arr)[None,]

        params = meta.get("parameters")
        prompt_json = meta.get("prompt")
        digest = _digest_comfy(prompt_json) if prompt_json else None

        lines = []
        positive = negative = ""
        seed = steps = None
        cfg = None

        if params:
            lines.append(params.strip())
            a = _parse_a1111(params)
            positive, negative = a["positive"], a["negative"]
            seed, steps, cfg = a["seed"], a["steps"], a["cfg"]

        if digest:
            if not params:
                # ComfyUI形式からA1111風のダイジェストを組み立てる
                positive = str(digest["positive"] or "")
                negative = str(digest["negative"] or "")
                seed, steps = digest["seed"], digest["steps"]
                cfg = digest["cfg"]
                lines.append(positive)
                if negative:
                    lines.append("Negative prompt: " + negative)
                for i, p in enumerate(digest["passes"]):
                    tag = u"" if i == 0 else u"[pass%d %s] " % (i + 1,
                                                                p["class"])
                    kv = []
                    if p["steps"] is not None:
                        kv.append("Steps: %d" % p["steps"])
                    if p["sampler"]:
                        kv.append("Sampler: %s" % p["sampler"])
                    if p["scheduler"]:
                        kv.append("Scheduler: %s" % p["scheduler"])
                    if p["cfg"] is not None:
                        kv.append("CFG scale: %s" % p["cfg"])
                    if p["seed"] is not None:
                        kv.append("Seed: %d" % p["seed"])
                    if p["denoise"] is not None:
                        kv.append("Denoise: %s" % p["denoise"])
                    if kv:
                        lines.append(tag + ", ".join(kv))
                if digest["width"] and digest["height"]:
                    lines.append("Size: %dx%d" % (digest["width"],
                                                  digest["height"]))
            if digest["models"]:
                lines.append("Model: " + ", ".join(digest["models"]))
            if digest["loras"]:
                lines.append("LoRA: " + ", ".join(digest["loras"]))
            # 型付き出力の穴をグラフ解析側で埋める
            if not positive:
                positive = str(digest["positive"] or "")
            if not negative:
                negative = str(digest["negative"] or "")
            if seed is None:
                seed = digest["seed"]
            if steps is None:
                steps = digest["steps"]
            if cfg is None:
                cfg = digest["cfg"]

        if prompt_json:
            lines.append(u"--\nComfyUIワークフロー埋め込みあり"
                         u"(この画像をキャンバスにドロップすると完全復元)")
        if not lines:
            lines.append(u"メタデータが見つかりません"
                         u"(対応: A1111形式parameters / ComfyUI埋め込み)")

        info = "\n".join(lines)
        return (tensor, info, positive or "", negative or "",
                int(seed) if seed is not None else 0,
                int(steps) if steps is not None else 0,
                float(cfg) if cfg is not None else 0.0)

    @classmethod
    def IS_CHANGED(cls, image):
        path = folder_paths.get_annotated_filepath(image)
        m = hashlib.sha256()
        with open(path, "rb") as f:
            m.update(f.read())
        return m.hexdigest()

    @classmethod
    def VALIDATE_INPUTS(cls, image):
        if not folder_paths.exists_annotated_filepath(image):
            return "Invalid image file: {}".format(image)
        return True


NODE_CLASS_MAPPINGS = {
    "PNGInfoReadable": PNGInfoReadable,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PNGInfoReadable": "PNG Info (Readable)",
}
