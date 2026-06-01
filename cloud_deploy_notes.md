# Streamlit Community Cloud 上云步骤

这份说明给非技术用户照着做。目标是：代码和 `holdings_data.json` 放到 GitHub 私有仓库；手机网页里改持仓后，应用自动把新文件提交回这个私有仓库。

参考官方文档：

- Streamlit Community Cloud 可以连接 GitHub 的公开或私有仓库。
- 在 share.streamlit.io 里点 `Create app`，选择仓库、分支和入口文件即可部署。
- 私有仓库部署的应用默认按权限控制，也可以在 App settings -> Sharing 里选择 `Only specific people can view this app`。

## 1. 上传前先确认

项目里这些文件不要上传到 GitHub：

- `stock_data.db`：本地数据库缓存。
- `backups/`：历史备份。
- `optional/notify_config.json`：提醒配置。
- `__pycache__/`：Python 缓存。

这些已写进 `.gitignore`。

`holdings_data.json` 现在需要上传到私有仓库，手机端保存持仓时会自动更新它。

## 2. 建 GitHub 私有仓库

1. 打开 GitHub。
2. 新建仓库，名字可叫 `stock-dashboard`。
3. Repository visibility 选 `Private`。
4. 不要勾选添加 `README`、`.gitignore` 或 `license`，保持空仓库，避免第一次推送冲突。
5. 建好后，复制 GitHub 显示的仓库地址，例如：

```text
https://github.com/你的用户名/stock-dashboard.git
```

6. 如果电脑已安装 Git，在 `C:\stock` 目录执行：

```powershell
git init
git add .
git commit -m "Initial stock dashboard"
git branch -M main
git remote add origin https://github.com/你的用户名/stock-dashboard.git
git push -u origin main
```

7. 推送时 GitHub 现在不能用账号密码登录，需要用 Personal Access Token。
8. 上传后再看一眼：仓库里应该有 `holdings_data.json`，但不应该有 `stock_data.db`。

## 3. 部署到 Streamlit Community Cloud

1. 打开 `https://share.streamlit.io`。
2. 用 GitHub 登录。
3. 第一次使用时，允许 Streamlit 访问你的 GitHub 仓库；如果是私有仓库，需要授权访问 private repositories。
4. 点右上角 `Create app`。
5. 选择 `Yup, I have an app`。
6. Repository 选择你的私有仓库。
7. Branch 选择 `main`。
8. Main file path 填：

```text
stock_app.py
```

9. 打开 Advanced settings / Secrets，填入 GitHub 写回用的 Token：

```toml
GH_TOKEN = "把你的 GitHub Personal Access Token 粘贴到这里"
```

10. 如果以后想覆盖默认仓库信息，也可以额外填：

```toml
GH_OWNER = "lwy13124975937-png"
GH_REPO = "stock-dashboard"
GH_BRANCH = "main"
GH_PATH = "holdings_data.json"
```

11. 点 `Deploy`。
12. 等几分钟，页面会生成一个 `streamlit.app` 网址。

## 4. 设置仅自己可见

1. 进入 Streamlit 应用页面。
2. 打开 App settings。
3. 找到 Sharing。
4. 选择 `Only specific people can view this app`。
5. 只添加你自己的邮箱，或你允许查看的人。

注意：Streamlit Community Cloud 私有应用数量可能有限制；如果平台提示限制，以页面提示为准。

## 5. 首次部署后录入持仓

因为 `holdings_data.json` 已经在私有仓库里，云端打开后会直接读取这份持仓。

1. 打开你的 Streamlit 页面。
2. 进入 `高级功能`。
3. 选择 `持仓管理`。
4. 按账户进入：
   - 银河证券
   - 东方财富
   - 支付宝
5. 点 `添加一只到 xxx`。
6. 选择资产类型：
   - A股个股
   - 场内基金/LOF
   - 场外基金
7. 填名称、代码、成本价、份额；场外基金填当前市值和持有收益。
8. 点 `添加并保存`。
9. 系统会先写入运行环境里的 `holdings_data.json`，再通过 GitHub API 提交回私有仓库。
10. 保存成功后，应用会提示“已保存并同步到云端”，约 1 分钟后刷新就是最新数据。

## 6. 重要限制

- Streamlit Community Cloud 不适合做真正的每日定时任务，所以每日收益快照交给 GitHub Actions。
- 当前页面打开时会读取行情；板块长期情绪需要 `board_heat` 连续多日数据，否则会显示“历史不足”。
- 手机端保存持仓会写回 GitHub 私有仓库；只要 `GH_TOKEN` 配好，重启后也能读到最新持仓。

## 7. GitHub Actions 每日快照怎么用

项目里已经有 `.github/workflows/daily.yml`。它会在周一到周五北京时间约 15:40 自动运行一次：安装依赖、读取仓库里的 `holdings_data.json`、运行 `daily_snapshot.py`，再把 `history/` 里的收益历史和板块历史提交回仓库。

还要打开自动提交权限：

1. GitHub 仓库进入 `Settings`。
2. 点左侧 `Actions` -> `General`。
3. 找到 `Workflow permissions`。
4. 选择 `Read and write permissions`。
5. 点 `Save`。

以后自动任务生成的是 `history/snapshots.csv` 和 `history/board_heat.csv`，这两个文件会提交到 Git；手机端改持仓时，`holdings_data.json` 也会被应用提交回私有仓库。

## 8. Personal Access Token 怎么生成

GitHub 推送代码时如果弹出登录框，密码位置不要填 GitHub 密码，要填 Token。

1. GitHub 右上角头像 -> `Settings`。
2. 左侧最下面 `Developer settings`。
3. `Personal access tokens` -> `Tokens (classic)`。
4. 点 `Generate new token`。
5. 勾选 `repo` 权限。
6. 生成后马上复制保存；这个 token 只显示一次。
7. 推送代码时，用户名填 GitHub 用户名，密码填这个 token。

## 9. 手机怎么看

1. Streamlit 部署成功后，复制 `.streamlit.app` 网址。
2. 用手机浏览器打开。
3. 登录允许访问的人对应的账号。
4. 添加到浏览器书签或手机桌面。
5. 免费版长时间不用会休眠，第一次打开等十几秒是正常的。
