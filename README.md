# ComfyUI-PNGInfo

A1111の「PNG Info」の気軽さをComfyUIに持ってくるノード。
画像やSafetensorsモデルファイルのメタデータを**人間が読める形**で表示し、不要な情報のクリーンアップや主要な値の再利用が行えます。

> **English**: A1111-style "PNG Info" and Safetensors Metadata Tools for ComfyUI. Reads metadata from both images and model files, shows it as human-readable text, cleans unnecessary metadata, and exposes outputs for workflow reuse.

---

## 収録ノード (Included Nodes)

### 1. PNG Info (Readable)

画像の生成メタデータをA1111のPNG Info風に読みやすいテキストで出力し、主要な設定値を型付きピンで取り出せます。

| 出力 | 型 | 内容 |
|---|---|---|
| image | IMAGE | 読み込んだ画像(そのまま使える) |
| info | STRING | A1111のPNG Info風の読めるテキスト |
| positive / negative | STRING | プロンプト |
| seed / steps | INT | シード / ステップ数 |
| cfg | FLOAT | CFGスケール |

* **info を showAnything 系ノード**(easy-use / pythongosssss等)に繋ぐと画面で読めます。
* **A1111形式**(`parameters`テキスト)と**ComfyUI形式**(埋め込みワークフローJSON)の両対応。
* ComfyUI形式はグラフを自動解析し、KSamplerのパラメータ、モデル名、LoRA名、解像度などをベストエフォートで逆引きして表示します。

---

### 2. Safetensors Info (Readable) [NEW]

Safetensors形式のモデル（Checkpoints、LoRA、VAEなど）に含まれるメタデータを**ロードせずに瞬時に**読み取って表示します。

| 出力 | 型 | 内容 |
|---|---|---|
| info | STRING | マージ情報や学習設定などを整形したテキスト |
| json | STRING | メタデータの生のJSON文字列 |

* テンソル（重みデータ）自体は一切ロードしないため、何GBもあるモデルでもメモリを消費せず一瞬で読み込みが完了します。
* Kohya_ssなどで学習されたLoRAの学習設定（学習率、フォルダパスなど）や、マージモデルのマージレシピを確認するのに便利です。

---

### 3. Safetensors Metadata Cleaner [NEW]

Safetensorsファイルのメタデータから不要な情報（マージ履歴、個人情報になり得るローカルPCのファイルパスなど）を除去して、保存します。

* **メモリを消費しない安全設計**: 重みデータをメモリに展開せず、バイナリを直接ストリームコピーするため、低メモリな環境でもフリーズせずに一瞬で処理が完了します。
* **柔軟な削除ルール**: 
  * `remove_all_metadata` が True の場合はメタデータを完全に消去します。
  * False にすると、`remove_keys_by_regex` に指定した正規表現（デフォルト: `.*path.*|.*hash.*`）にマッチする不要なキーだけを狙い撃ちで削除できます（トリガーワードなどの有益な情報は残せます）。
* **上書き保存オプション (`overwrite`) [NEW]**:
  * `overwrite` を True に設定すると、`save_name` の指定は無視され、入力ファイルを直接上書きクリーンアップします。
  * **既存のチェックポイント/LoRA保存ノードの出力（STRING型のファイルパス）を `custom_path` に直接繋いで、自動で上書きクリーンアップする連携が可能**です。
* **カスタムメタデータの追加**: `custom_metadata_json` に任意のJSONを入力することで、著作権情報や独自のメタデータをモデルに追加して保存できます。

---

## 既存の保存ノードと連携して自動クリーンアップするワークフローの組み方

1. マージモデルなどを保存する既存の保存ノード（パスをSTRING出力できるもの）を用意します。
2. 保存ノードのパス出力ピンを、`Safetensors Metadata Cleaner` の **`custom_path`** に接続します。
3. `Safetensors Metadata Cleaner` の `overwrite` を **True** に設定します。
4. これにより、既存ノードによってファイルが保存された直後に、そのファイルを自動でクリーンアップします（無駄な中間ファイルは生成されません）。

> [!NOTE]
> クリーンアップ処理は、数GB〜十数GBのモデルファイルのコピー/上書きを行うため、実行中（数秒〜数十秒）はComfyUIが一時的に応答しなくなります（フリーズしたようになります）。これはComfyUIがすべての処理を同期的に実行する仕様であるためで、正常な動作です。処理が完了すれば自動的に元に戻ります。

---

## インストール (Installation)

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/galigali-san/ComfyUI-PNGInfo
```

依存ライブラリはありません（標準ライブラリのみで動作します）。
再起動後、`image` カテゴリに以下の3つのノードが追加されます：
* `PNG Info (Readable)`
* `Safetensors Info (Readable)`
* `Safetensors Metadata Cleaner`

## ライセンス (License)

MIT License — Copyright (c) 2026 galigali
