# PVESPS BI Maintenance Dashboard

A maintenance-oriented Power BI dashboard extended from the **PVESPS solar power forecasting project**。  
本作品不是另外製作新的分析題目，而是將既有的資料工程 / 預測成果，整理成更貼近實務情境的 **BI 維護型展示版**。

---

## 1. Project Overview｜專案概述

**PVESPS BI Maintenance Dashboard** 是從原本的 **PVESPS（太陽光電發電預測與效能分析系統）** 延伸出的 Power BI 展示專案。

這份作品的重點，不是單純做一份漂亮的 Dashboard，而是模擬在實務情境中：

- 如何接手既有的資料平台 / 預測系統
- 如何整理既有資料輸出成 BI 可消費的資料集
- 如何從總覽、異常監控到維運紀錄，建立一份可被使用、可被追查、也可被維護的報表

換句話說，這份作品希望展示的不是「我會畫圖表」，而是：

> 我可以把資料工程與預測系統的成果，整理成一份真正有維護價值的 BI Dashboard。

---

## 2. Project Positioning｜專案定位

本作品定位為：

- **Power BI 維護型展示版**
- **BI-consumable monitoring layer**
- **Data Quality + Maintenance-oriented dashboard**

相較於一般偏分析型、單次性展示的報表，本專案更強調：

- **Operational Visibility｜營運總覽**
- **Data Quality Monitoring｜資料品質監控**
- **Pipeline Traceability｜流程追蹤**
- **Maintenance-friendly Reporting｜可維護報表設計**

---

## 3. Why This Project Matters｜作品價值

這份作品的價值，在於它並不是從零發想一個新的 BI 題目，而是直接延伸自既有的 PVESPS 專案成果，將：

- prediction results
- site dimension data
- ETL / scoring run logs

整理成 Power BI 可直接使用的 View 與 Dashboard 頁面。

因此它更能反映實務中的工作情境：

> 不是「我自己做一份分析報表」，  
> 而是「我接手一套已有資料流程的系統，並把它整理成更可閱讀、可監控、可維護的 BI 介面」。

---

## 4. Project Goals｜專案目標

此展示版主要回答三個問題：

1. **目前預測發電量與實際發電量的差異如何？**
2. **哪些站點或資料列屬於高風險誤差，需要優先關注？**
3. **資料流程與預測 pipeline 是否有正常執行，若異常能否追查？**

---

## 5. Data Sources｜資料來源

本 Dashboard 並非手工整理靜態資料，而是直接延伸自 PVESPS 專案中的既有資料表與執行紀錄，並額外整理出 Power BI 可直接使用的 BI 消費層 View。

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

本 Dashboard 分成三頁：

1. **Overview**
2. **Data Quality Monitor**
3. **Maintenance Log**

這三頁對應三種不同的使用視角：

- 經營 / 管理視角
- 異常監控視角
- 維運追查視角

---

## 7. Page 1 — Overview

### Purpose｜頁面目的
此頁為管理與營運總覽頁，主要用來快速掌握：

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
- 明細表與日期 / 城市 slicer

### Business Meaning｜商業意義
此頁的目的，是讓使用者先從整體角度掌握系統表現，再往下進一步查看異常與細節。

---

## 8. Page 2 — Data Quality Monitor

### Purpose｜頁面目的
此頁聚焦資料品質與異常監控，主要呈現：

- Missing Actual Count
- Average Absolute Error
- Average Error %
- Error Level Distribution
- Site Error Ranking
- Exception Detail

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
此頁模擬接手既有 BI 系統後，如何快速辨識：

- 高風險站點
- 中高誤差資料列
- 優先檢查目標

### Design Intention｜設計意圖
這頁的重點不在「漂亮圖表」，而在於：

> 用資料品質監控視角，快速定位值得被優先檢查的資料與站點。

---

## 9. Page 3 — Maintenance Log

### Purpose｜頁面目的
此頁聚焦資料流程與模型執行維運，主要呈現：

- Latest Event Time
- Success / Running / Failed Run Count
- Run Status Distribution
- Pipeline Run Count
- Maintenance Log Detail

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
此頁用來展示報表不只看結果，也能追蹤：

- pipeline 執行狀態
- 成功 / 失敗次數
- 輸入 / 輸出筆數
- 錯誤訊息與維運紀錄

這讓整份作品更接近實務上的 **BI 維護型系統**。

---

## 10. BI Design Thinking｜報表設計思路

這份作品背後的設計思路，不是「做三頁漂亮報表」，而是讓三頁對應三種實際工作問題：

### Overview
> 系統整體表現如何？

### Data Quality Monitor
> 哪些資料或站點值得優先檢查？

### Maintenance Log
> 若結果異常，能不能往回追查資料流程與執行紀錄？

因此，這份作品真正展示的是：

- 從資料到決策的整理能力
- 從異常到追查的維運思維
- 從工程輸出到 BI 消費層的轉換能力

---

## 11. What This Project Demonstrates｜本作品展示的能力

透過這份延伸展示版，我希望呈現的不只是 Power BI 視覺化能力，而是以下幾點：

### Data Product Thinking
- 能將既有資料工程專案整理為 BI 可消費資料集
- 能將技術輸出轉成管理 / 維運可讀資訊

### BI Reporting Design
- 能從資料總覽、異常監控、維運紀錄三個層次設計報表
- 能依不同使用者視角安排 dashboard 結構

### Data Quality Awareness
- 能理解資料品質與監控在實務中的重要性
- 能從資料誤差與異常分級角度設計監控頁面

### Maintenance Mindset
- 能從工程角度思考報表如何支援維護與追查，而不只是展示數字
- 能結合 log、pipeline status 與 business-facing dashboard

---

## 12. Project Value Summary｜專案價值總結

這份作品不是單純的 Dashboard 練習，而是：

> 將資料工程 / 預測系統成果，延伸為一個可用於實務情境的 BI 維護型展示版。

相較於一般只展示分析圖表的作品，本專案更強調：

- **資料來源可追溯**
- **異常可辨識**
- **流程可追查**
- **報表可維護**

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
