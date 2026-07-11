# ComfyUI-PNGInfo

A1111の「PNG Info」の気軽さをComfyUIに持ってくるノード。
画像のメタデータを**人間が読める形**で表示し、主要な値を**型付き出力**で再利用できる。

> **English**: A1111-style "PNG Info" for ComfyUI. Loads an image, shows its
> generation metadata as human-readable text (works with both A1111
> `parameters` text and ComfyUI embedded workflows), and exposes
> positive / negative / seed / steps / cfg as typed outputs so you can wire
> values from an old image straight into your current workflow.

## PNG Info (Readable)

| 出力 | 型 | 内容 |
|---|---|---|
| image | IMAGE | 読み込んだ画像(そのまま使える) |
| info | STRING | A1111のPNG Info風の読めるテキスト |
| positive / negative | STRING | プロンプト |
| seed / steps | INT | シード / ステップ数 |
| cfg | FLOAT | CFGスケール |

- **info を showAnything 系ノード**(easy-use / pythongosssss等)に繋ぐと画面で読める
- **A1111形式**(`parameters`テキスト)と**ComfyUI形式**(埋め込みワークフローJSON)の両対応。両方あればA1111形式を優先
- ComfyUI形式はグラフを解析して、KSampler系のパス(hires/Detailer含む)・モデル名・LoRA・サイズを抽出する。プリミティブやスイッチ、文字列加工ノードを経由した値もリンクを遡って探す(ベストエフォート。複雑すぎる場合は取れない項目もある)
- 「完全な再現」は従来通り**画像をキャンバスにドラッグ&ドロップ**(ComfyUI標準機能)。このノードは「**中身をサッと読む・一部だけ今のワークフローに移す**」ため

## インストール

```
cd ComfyUI/custom_nodes
git clone https://github.com/galigali-san/ComfyUI-PNGInfo
```

依存ライブラリなし。再起動後、`image` カテゴリに「PNG Info (Readable)」が入る。

## ライセンス

MIT License — Copyright (c) 2026 galigali
