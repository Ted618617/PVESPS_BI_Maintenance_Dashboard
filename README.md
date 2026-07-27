# PVESPS BI Maintenance Dashboard

This is a maintenance-oriented Power BI dashboard extended from the **PVESPS solar power forecasting project**.

這個專案是從原本的 PVESPS 太陽光電發電預測專案延伸出來的 Power BI 展示版。  
我不是另外做一個新的分析題目，而是把既有的資料工程、預測結果與執行紀錄整理成 BI 可以使用的資料集，並設計成比較接近實務維運情境的 Dashboard。

---

## 1. Project Overview｜專案概述

**PVESPS BI Maintenance Dashboard** 是 **PVESPS（Photovoltaic Energy Prediction & Solar Power System）** 的 Power BI 延伸專案。

原本的 PVESPS 主要著重在資料整合、ETL、發電量預測與模型結果產出；這個 BI 版本則是接著思考：

- 如果資料流程已經存在，後續要如何監控？
- 如果預測結果出現偏差，使用者要怎麼快速找到異常來源？
- 如果 ETL 或 scoring pipeline 發生問題，維運人員是否能從報表中追查？

因此，這份 Dashboard 的重點不只是視覺化，而是將資料工程與預測流程的輸出，整理成一個可閱讀、可追蹤、也比較容易維護的 BI 介面。

---

## 2. Project Positioning｜專案定位

這個專案的定位是：

- **Power BI maintenance dashboard**
- **BI-consumable monitoring layer**
- **Data Quality + Pipeline Monitoring dashboard**

相較於只看單次分析結果的報表，這個版本更偏向維運與監控用途，主要聚焦在：

- **Operational Visibility｜營運總覽**
- **Data Quality Monitoring｜資料品質監控**
- **Pipeline Traceability｜流程追蹤**
- **Maintenance-friendly Reporting｜維護友善的報表設計**

---

## 3. Why This Project Matters｜作品價值

在實務工作中，很多 BI 報表並不是從零開始設計，而是接手既有系統、既有資料流程或既有資料表之後，再整理成使用者能理解的資訊。

這個專案就是模擬這種情境。

我將 PVESPS 中已經產出的：

- prediction results
- site dimension data
- ETL run logs
- model scoring run logs

整理成 Power BI 可使用的 View 與資料集，並設計成 Dashboard。

這個延伸版想呈現的是：

> 不只是把資料畫成圖表，而是把資料工程與預測系統的輸出，整理成一份能協助維運與追查問題的 BI 報表。

---

## 4. Project Goals｜專案目標

這份 Dashboard 主要回答三個問題：

1. **目前預測發電量與實際發電量的差異如何？**
2. **哪些站點或資料列屬於高風險誤差，需要優先檢查？**
3. **資料流程與預測 pipeline 是否正常執行？如果異常，能否追查？**

---

## 5. Data Sources｜資料來源

本 Dashboard 並不是手工整理靜態 Excel，而是延伸自 PVESPS 專案中的既有資料表與執行紀錄。

為了讓 Power BI 比較容易使用，我另外整理出 BI 消費層 View，讓報表可以直接連接整理後的資料。

### 5.1 Main Source Tables｜主要來源表

- `mart.fact_generation_prediction_daily`
- `mart.dim_solar_site`
- `meta.model_scoring_run_log`
- `meta.etl_run_log`

### 5.2 Power BI Views｜Power BI 使用的 View

- `mart.v_powerbi_prediction_dataset`
- `meta.v_powerbi_maintenance_log`

---

## 6. Dashboard Structure｜Dashboard 頁面結構

本 Dashboard 目前分成三頁：

1. **Overview**
2. **Data Quality Monitor**
3. **Maintenance Log**

三個頁面分別對應不同的使用情境：

- 管理者想先看整體表現
- 分析或維運人員想找出高風險資料
- 工程或維運人員想追查 pipeline 執行狀態

---

## 7. Page 1 — Overview

### Purpose｜頁面目的

Overview 頁面主要用來快速掌握整體預測表現。

這一頁不先處理細節問題，而是讓使用者先看到整體狀況，例如：

- 總預測發電量
- 總實際發電量
- 平均絕對誤差率
- 預測資料筆數
- predicted vs actual 趨勢
- 站點級誤差排行
- 明細資料下鑽

### Key Visuals｜主要視覺元件

- KPI cards
  - Total Predicted Generation
  - Total Actual Generation
  - Avg Absolute Error %
  - Prediction Records
- `Prediction vs Actual Trend`
- `Top Site Error Ranking`
- 明細表
- 日期 / 城市 slicer

### Business Meaning｜商業意義

這一頁的用途，是讓使用者先判斷系統目前的整體表現是否合理，再決定是否需要往下檢查異常站點或明細資料。

---

## 8. Page 2 — Data Quality Monitor

### Purpose｜頁面目的

Data Quality Monitor 頁面主要用來檢查資料品質與預測異常。

在資料平台或預測系統中，結果異常不一定代表模型不好，也可能來自資料缺漏、來源異常、站點資料不完整或某些批次資料處理問題。

因此這一頁的重點是協助使用者快速找出：

- 哪些資料缺漏
- 哪些站點誤差較高
- 哪些資料列需要優先檢查

### Key Visuals｜主要視覺元件

- KPI cards
  - Missing Actual Count
  - Average Absolute Error
  - Average Error %
- `Error Level Distribution`
- `Site Error Ranking`
- 異常資料明細表
- `error_level` slicer

### Monitoring Focus｜監控重點

這一頁主要模擬接手既有 BI 或資料平台後，如何用資料品質的角度快速辨識：

- 高風險站點
- 中高誤差資料列
- 需要優先排查的資料來源或預測結果

### Design Intention｜設計意圖

這一頁的設計重點不是做複雜圖表，而是讓維運或分析人員可以快速回答：

> 哪些資料最需要先看？

---

## 9. Page 3 — Maintenance Log

### Purpose｜頁面目的

Maintenance Log 頁面主要用來追蹤 ETL 與 model scoring pipeline 的執行狀態。

如果 Overview 或 Data Quality Monitor 發現異常，使用者可以進一步回到 Maintenance Log，確認是否有 pipeline 失敗、執行中斷、輸入輸出筆數異常或錯誤訊息。

### Key Visuals｜主要視覺元件

- KPI cards
  - Latest Event Time
  - Success Run Count
  - Running Run Count
  - Failed Run Count
- `Run Status Distribution`
- `Pipeline Run Count`
- `Maintenance Log Detail`
- `Status Filter`

### Operational Value｜維運價值

這一頁讓 Dashboard 不只是看結果，也能往回追查資料流程。

主要可檢查：

- pipeline 執行狀態
- 成功 / 失敗次數
- 輸入 / 輸出筆數
- 錯誤訊息
- 維運紀錄

這樣的設計比較接近實務上的 BI 維運報表，而不是一次性的展示型圖表。

---

## 10. BI Design Thinking｜報表設計思路

這份 Dashboard 的設計邏輯，是讓三個頁面對應三個實際工作問題。

### Overview

> 系統整體表現如何？

### Data Quality Monitor

> 哪些資料或站點值得優先檢查？

### Maintenance Log

> 如果結果異常，能不能回頭追查資料流程與執行紀錄？

因此，這份作品主要想展示的是：

- 如何把資料工程輸出整理成 BI 可使用資料集
- 如何用 Dashboard 支援異常檢查與維運追查
- 如何把技術性資料轉成使用者能理解的管理資訊

---

## 11. What This Project Demonstrates｜本作品展示的能力

這個延伸版主要展示以下幾個能力。

### Data Product Thinking

- 將既有資料工程專案整理成 BI 可消費資料集
- 將預測結果、站點資料與執行 log 串成可閱讀的報表資料

### BI Reporting Design

- 從總覽、異常監控、維運紀錄三個層次安排 Dashboard
- 依不同使用者需求設計頁面結構與指標呈現方式

### Data Quality Awareness

- 以缺漏、誤差、異常分級的角度設計監控頁面
- 協助使用者找出需要優先檢查的站點與資料列

### Maintenance Mindset

- 將 pipeline status、run log 與 Dashboard 結合
- 讓報表不只展示數字，也能支援維護與問題追查

---

## 12. Project Value Summary｜專案價值總結

這個專案不是單純的 Power BI 練習，而是把 PVESPS 的資料工程與預測成果，延伸成一個比較接近實務維運情境的 BI Dashboard。

相較於一般只展示分析結果的報表，本專案更強調：

- **資料來源可追溯**
- **異常資料可辨識**
- **Pipeline 狀態可追查**
- **報表結構可維護**

整體來說，這份作品想呈現的是：  
我不只會做 Dashboard，也能從資料流程、資料品質與維運角度思考報表該如何設計。

---

## 13. Folder Structure｜資料夾結構

```text
Step_11_BI儀表板維護/
├─ data/
│  ├─ powerbi_prediction_dataset.csv
│  └─ powerbi_maintenance_log.csv
├─ docs/
│  ├─ overview.png
│  ├─ data_quality_monitor.png
│  └─ maintenance_log.png
├─ powerbi/
│  └─ PVESPS_BI_Maintenance.pbix
└─ README.md
```
