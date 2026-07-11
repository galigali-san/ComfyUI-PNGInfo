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


import struct

_FOLDERS = ["checkpoints", "loras", "vae", "diffusion_models", "unet",
            "text_encoders", "clip", "embeddings", "controlnet",
            "upscale_models"]

_SEP = " :: "

def _list_safetensors_files():
    seen = set()
    choices = []
    for folder in _FOLDERS:
        try:
            names = folder_paths.get_filename_list(folder)
        except Exception:
            continue
        for name in names:
            if not name.lower().endswith((".safetensors", ".sft")):
                continue
            full = folder_paths.get_full_path(folder, name)
            if full in seen:
                continue
            seen.add(full)
            choices.append(folder + _SEP + name)
    return choices or ["(safetensorsが見つかりません)"]


class SafetensorsInfoReadable:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "file": (_list_safetensors_files(),),
            },
            "optional": {
                "custom_path": ("STRING", {
                    "default": "",
                    "tooltip": "ここにフルパスを入れると、上の選択より優先してそのファイルを読む"
                }),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("info", "json")
    FUNCTION = "read"
    CATEGORY = "image"
    DESCRIPTION = "Safetensorsのメタデータ（マージ情報や学習設定など）をロードせずに瞬時に読み取って表示します。"

    def read(self, file, custom_path=""):
        custom_path = custom_path.strip().strip('"').strip("'")
        if custom_path:
            path = custom_path
        else:
            if _SEP not in file:
                raise ValueError("モデルフォルダにsafetensorsがありません。custom_pathにフルパスを入れてください")
            folder, name = file.split(_SEP, 1)
            path = folder_paths.get_full_path(folder, name)
            
        if not path or not os.path.isfile(path):
            raise ValueError(f"ファイルが見つかりません: {path}")

        meta, tensor_count = self.read_header(path)
        report = self.build_report(path, meta, tensor_count)
        js = self.metadata_json(meta)
        return (report, js)

    def read_header(self, path):
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(8)
            if len(head) < 8:
                raise ValueError("ファイルが小さすぎます")
            n = struct.unpack("<Q", head)[0]
            if n <= 0 or n > size or n > 100 * 1024 * 1024:
                raise ValueError("ヘッダサイズが不正です")
            try:
                header = json.loads(f.read(n).decode("utf-8"))
            except Exception:
                raise ValueError("ヘッダがJSONとして読めません")
        meta = header.get("__metadata__", {}) or {}
        tensor_count = len([k for k in header if k != "__metadata__"])
        return meta, tensor_count

    def build_report(self, path, meta, tensor_count):
        lines = [
            f"file: {os.path.basename(path)}",
            f"path: {path}",
            f"size: {self.format_bytes(os.path.getsize(path))}",
            f"tensors: {tensor_count}",
            f"metadata: {len(meta)} items",
        ]
        if not meta:
            lines.append("\n__metadata__ は空です")
        for k in sorted(meta.keys()):
            v = meta[k]
            parsed = self.try_parse_json(v)
            lines.append(f"\n--- {k} ---")
            if parsed is not None:
                lines.append(json.dumps(parsed, ensure_ascii=False, indent=2))
            else:
                lines.append(str(v))
        return "\n".join(lines)

    def metadata_json(self, meta):
        out = {}
        for k, v in meta.items():
            parsed = self.try_parse_json(v)
            out[k] = parsed if parsed is not None else v
        return json.dumps(out, ensure_ascii=False, indent=2)

    @staticmethod
    def try_parse_json(s):
        if not isinstance(s, str):
            return None
        t = s.strip()
        if not (t.startswith("{") or t.startswith("[")):
            return None
        try:
            return json.loads(t)
        except Exception:
            return None

    @staticmethod
    def format_bytes(n):
        n = float(n)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024.0 or unit == "TB":
                return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
            n /= 1024.0

    @classmethod
    def IS_CHANGED(cls, file, custom_path=""):
        try:
            custom_path = custom_path.strip().strip('"').strip("'")
            if custom_path:
                path = custom_path
            else:
                folder, name = file.split(_SEP, 1)
                path = folder_paths.get_full_path(folder, name)
            return f"{path}:{os.path.getmtime(path)}"
        except Exception:
            return float("nan")


class SafetensorsMetadataCleaner:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "file": (_list_safetensors_files(),),
                "save_name": ("STRING", {"default": "cleaned_model.safetensors"}),
                "remove_all_metadata": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "custom_path": ("STRING", {"default": ""}),
                "remove_keys_by_regex": ("STRING", {
                    "default": ".*path.*|.*hash.*",
                    "tooltip": "remove_all_metadataがFalseの場合に、このパターンにマッチするキーを削除します。パイプ(|)で複数指定可。"
                }),
                "custom_metadata_json": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": '{\n  "author": "Anonymous"\n}',
                    "tooltip": "追加または上書きしたいメタデータをJSON形式で入力します。"
                }),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("saved_path",)
    FUNCTION = "clean"
    CATEGORY = "image"
    DESCRIPTION = "Safetensorsのメタデータから不要な情報（マージ履歴、ローカルパスなど）を除去して、新しいファイルとして保存します。メモリを消費しません。"

    def clean(self, file, save_name, remove_all_metadata, custom_path="", remove_keys_by_regex="", custom_metadata_json=""):
        custom_path = custom_path.strip().strip('"').strip("'")
        if custom_path:
            src_path = custom_path
        else:
            if _SEP not in file:
                raise ValueError("モデルフォルダにsafetensorsがありません。custom_pathにフルパスを入れてください")
            folder, name = file.split(_SEP, 1)
            src_path = folder_paths.get_full_path(folder, name)

        if not src_path or not os.path.isfile(src_path):
            raise ValueError(f"元ファイルが見つかりません: {src_path}")

        if not custom_path:
            folder, name = file.split(_SEP, 1)
            dest_dir = os.path.dirname(src_path)
        else:
            dest_dir = os.path.dirname(src_path)

        save_name = save_name.strip()
        if not save_name.lower().endswith((".safetensors", ".sft")):
            save_name += ".safetensors"
        dst_path = os.path.join(dest_dir, save_name)

        if os.path.abspath(src_path) == os.path.abspath(dst_path):
            raise ValueError("元ファイルと保存先ファイルが同じです。別名で保存してください。")

        with open(src_path, 'rb') as f_src:
            header_size_bytes = f_src.read(8)
            header_size = struct.unpack('<Q', header_size_bytes)[0]
            header_bytes = f_src.read(header_size)
            header = json.loads(header_bytes.decode('utf-8'))

        meta = header.get('__metadata__', {}) or {}
        
        if remove_all_metadata:
            new_meta = {}
        else:
            new_meta = meta.copy()
            if remove_keys_by_regex.strip():
                try:
                    pattern = re.compile(remove_keys_by_regex.strip(), re.IGNORECASE)
                    new_meta = {k: v for k, v in new_meta.items() if not pattern.search(k)}
                except Exception as e:
                    raise ValueError(f"正規表現パターンが無効です: {str(e)}")

        custom_metadata_json = custom_metadata_json.strip()
        if custom_metadata_json:
            try:
                custom_meta = json.loads(custom_metadata_json)
                if not isinstance(custom_meta, dict):
                    raise ValueError("カスタムメタデータはJSONオブジェクト形式である必要があります")
                new_meta.update(custom_meta)
            except Exception as e:
                raise ValueError(f"カスタムメタデータJSONの解析に失敗しました: {str(e)}")

        if not new_meta:
            if '__metadata__' in header:
                del header['__metadata__']
        else:
            header['__metadata__'] = new_meta

        new_header_str = json.dumps(header, separators=(',', ':'))
        new_header_bytes = new_header_str.encode('utf-8')

        current_len = 8 + len(new_header_bytes)
        padding_len = (8 - (current_len % 8)) % 8
        if padding_len > 0:
            new_header_bytes += b' ' * padding_len

        new_header_size = len(new_header_bytes)
        new_header_size_bytes = struct.pack('<Q', new_header_size)

        with open(dst_path, 'wb') as f_dst:
            f_dst.write(new_header_size_bytes)
            f_dst.write(new_header_bytes)

            with open(src_path, 'rb') as f_src:
                f_src.seek(8 + header_size)
                chunk_size = 64 * 1024 * 1024
                while True:
                    chunk = f_src.read(chunk_size)
                    if not chunk:
                        break
                    f_dst.write(chunk)

        print(f"[SafetensorsMetadataCleaner] クリーンアップされたファイルを保存しました: {dst_path}")
        return (dst_path,)


NODE_CLASS_MAPPINGS = {
    "PNGInfoReadable": PNGInfoReadable,
    "SafetensorsInfoReadable": SafetensorsInfoReadable,
    "SafetensorsMetadataCleaner": SafetensorsMetadataCleaner,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PNGInfoReadable": "PNG Info (Readable)",
    "SafetensorsInfoReadable": "Safetensors Info (Readable)",
    "SafetensorsMetadataCleaner": "Safetensors Metadata Cleaner",
}
