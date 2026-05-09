# 着席検知システム アイデアリスト

今後実装したい機能のアイデアを貯めておくファイル。

---

## 1. 着席時間の目標管理・進捗通知（2026-05-09 追記）

### 概要
1日の着席時間目標（例: 6時間）を設定し、現在の累積着席時間と達成率を定期通知する。

### 通知タイミング（案）
- 30分ごとのバッテリーチェックのタイミングに便乗（`_check_battery_alert()` と同じ箇所）

### 通知内容イメージ
```
📊 本日の着席進捗: 2時間34分 / 目標6時間（43%）
```

### 実装メモ
- 目標時間は `.env.config` の `DAILY_GOAL_MINUTES=360` で設定
- 累積時間は `db_models.calc_daily_summary()` を流用できる
- 通知先はリアルタイムch（WEBHOOK_URL）またはサマリーch（SUMMARY_WEBHOOK_URL）

---

## 2. 着席時に音楽を再生（2026-05-09 追記）

### 概要
着席が確定したタイミングで、Termux 上で音楽（または BGM）を自動再生する。

### 実装メモ
- `_commit_state(STATUS_SEATED, ...)` の中で `termux-media-player play <file>` を呼ぶ
- または `am broadcast` でAndroid のメディアプレイヤーをキック
- 離席確定時に `termux-media-player stop` で止める
- 曲ファイルは `/storage/emulated/0/Music/` 以下に置く想定

### 課題
- フォアグラウンドでないと再生できない可能性あり（ColorOS 制限）
- ScreenKeeper が画面をオンにしているので多分いける

