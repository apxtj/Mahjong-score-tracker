# Mahjong-score-tracker

麻雀の戦績を記録・集計・閲覧できる Flask 製の Web アプリです。  
プレイヤーごとの成績推移、順位率、素点統計、称号システムをまとめて管理できます。

## 主な機能

- 対局結果の入力
  - 4人分のスコアを入力して対局結果を登録できます
  - うま・おかの設定に対応しています
  - 入力したスコアは自動的に順位に並べ替えられ、最終スコアとして保存されます

- 成績一覧・検索
  - `/results` で対局結果一覧を表示できます
  - 日付・プレイヤー・順位で絞り込み可能です
  - 日ごとのゲーム一覧と、グラフ用の集計データを表示できます

- プレイヤー管理
  - プレイヤーの追加・編集・削除ができます
  - 各プレイヤーのプロフィールでは、総対戦数・平均着順・ラス回避率・素点統計などを確認できます

- 称号・バッジシステム
  - 条件を満たしたプレイヤーに称号を付与できます
  - プロフィール上で最大3つまで称号を装備できます
  - 称号の画像は Cloudinary へアップロードすることも可能です
  - ローカル開発時は `static/title_badges/` からも読み込めます

- 管理画面
  - `/admin/titles` で称号の追加・編集・削除ができます
  - 新規称号登録時にはデフォルト条件値が入るようにしています

## 現在の実装状況

- Flask で動作する Web アプリです
- ローカル開発では SQLite を使用します
- 本番環境では `DATABASE_URL` を設定すれば PostgreSQL などにも対応できます
- 画像アップロードは Cloudinary 対応です。`CLOUDINARY_URL` が設定されていれば Cloudinary へ保存されます
- 認証は現状、`login_required` が無効化された状態で動作しています。必要に応じて再有効化できます

## 使い方

### ローカル起動

```bash
python -m venv venv
source venv/bin/activate  # Windows なら venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

ブラウザで以下にアクセスします。

- `/` : 対局結果入力
- `/results` : 成績一覧
- `/players` : プレイヤー一覧
- `/profile/<player_id>` : プロフィール
- `/admin/titles` : 称号管理

### 環境変数

以下の環境変数を設定すると、動作環境に合わせて使い分けできます。

- `SECRET_KEY` : セッション用シークレットキー
- `DATABASE_URL` : DB 接続先
- `FLASK_DEBUG` : 開発時のデバッグモード
- `CLOUDINARY_URL` : Cloudinary 接続用 URL

## 主要ファイル

- `app.py` : ルーティング、対局入力、成績集計、称号管理のメイン処理
- `models.py` : Player / Game / Title / Achievement モデル
- `templates/` : Flask テンプレート群
- `static/` : CSS / JS / 画像など
- `db/` : DB 関連の SQL 定義

## DB 初期化SQL

このリポジトリには、現在のアプリ仕様に合わせた初期スキーマを `db/schema.sql` に用意しています。  
Supabase や PostgreSQL で新規に DB を作る場合は、この SQL をそのまま実行すればテーブル定義を作成できます。

### 使い方

1. Supabase の SQL Editor で `db/schema.sql` を開く
2. 内容をそのまま実行する
3. 必要に応じて `DATABASE_URL` を設定して Flask アプリを起動する

> この SQL はテーブル定義のみを作成します。初期データの投入は行いません。

## 備考

- 称号の条件は `Title` モデルに保存され、プレイヤーの成績に応じて自動判定されます
- 永続称号（`is_permanent`）は、一度解除したら履歴として残り続ける仕様です
- 画像の保存先は、Cloudinary を使う場合は外部ストレージ、ローカルでは `static/title_badges/` です

### 管理画面の認証

`/admin/titles` は管理者向け画面なので、Basic Auth で保護されています。

必要な環境変数:

- `ADMIN_PASSWORD` : 管理画面に入るためのパスワード（必須）
- `ADMIN_USERNAME` : 省略時は `admin` を使用

例:

```bash
export ADMIN_PASSWORD="your-strong-password"
export ADMIN_USERNAME="admin"
```

`ADMIN_PASSWORD` が設定されていない場合は、管理画面へのアクセスは `401 Unauthorized` になります。