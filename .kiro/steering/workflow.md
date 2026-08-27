# Pikmin GPS Spoofer — 工作規範

## 語言
- 用繁體中文回覆。

## 「三連」= commit push tag
當使用者說「三連」「三鍵套」或類似說法時，代表要依序做：commit → push → tag。

執行步驟（順序不可省略）：
1. 改完 code 後，先用 `python -c "import py_compile; py_compile.compile(r'<app.py 絕對路徑>', doraise=True); print('OK')"` 驗證能編譯。
2. `git add` 指定檔案（不要用 `git add .`）並 commit，訊息用英文、簡潔描述這次改動。
3. **打 tag 前一定要先看目前最新版號**：執行 `git tag --sort=-v:refname | Select-Object -First 5`，確認最新的 tag 是哪個，不要憑記憶亂猜。
4. 在最新版號後面遞增打新 tag（例如最新是 `v2.9` 就打 `v2.10`），除非使用者另有指定。
5. `git push origin main <新tag>` 一次推上 commit 和 tag。

如果打 tag 前發現版號重複或猜錯，先刪掉錯誤的 tag（本地 `git tag -d`、遠端 `git push origin :refs/tags/<tag>`）再重打正確版號。
