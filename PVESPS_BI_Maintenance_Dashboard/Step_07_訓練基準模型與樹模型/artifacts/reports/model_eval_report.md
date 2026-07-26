# PVESPS Step_07¡UModel Evaluation Report v1

## 1. Objective

This step evaluates baseline machine learning models for predicting **daily solar generation (kWh)** at the site level.

The goal is not to find the most complex model, but to verify whether the training dataset built in Step_06 can support a meaningful regression workflow and outperform a simple naive baseline.

---

## 2. Prediction Target

- **Target**: `target_generation_kwh`
- **Granularity**: one row per **site ¡Ñ day**
- **Task type**: supervised regression

At this stage, the target label is based on **simulated actual generation**, which was derived from the existing generation estimate table plus adjustment rules.  
This setup is used to validate the end-to-end ML pipeline before introducing real measured generation data.

---

## 3. Input Dataset

Source table:

- `mart.ml_training_generation_daily`

The dataset includes:

- time features  
  - `month_num`, `day_num`, `weekday_num`, `season_code`, etc.
- site static features  
  - `install_area_ping`, `capacity_kw`, `panel_efficiency`
- sunshine-related features  
  - `sunshine_hours`
- rule-based generation features  
  - `estimated_generation_rule_kwh`
- historical lag features  
  - `lag_1_generation_kwh`
  - `lag_3_avg_generation_kwh`
  - `lag_7_avg_generation_kwh`
  - `lag_14_avg_generation_kwh`

Only rows with:

- `is_valid_for_training = TRUE`
- `target_available = TRUE`

were used for model training and evaluation.

---

## 4. Train / Validation / Test Split

A **time-based split** was used instead of random sampling, to better reflect real forecasting scenarios.

### Split summary

- **Train**
  - rows: 260
  - date range: 2025-12-17 ~ 2026-02-19

- **Validation**
  - rows: 56
  - date range: 2026-02-20 ~ 2026-03-05

- **Test**
  - rows: 56
  - date range: 2026-03-06 ~ 2026-03-19

This design prevents future information leakage and is more appropriate for time-series-like operational data.

---

## 5. Models Compared

Three baseline models were compared:

### 5.1 Naive Baseline
Prediction rule:

- use `lag_1_generation_kwh`
- if missing, fallback to `lag_3_avg_generation_kwh`
- if still missing, fallback to `estimated_generation_rule_kwh`

This model serves as the minimum benchmark.

### 5.2 Linear Regression
A simple linear model was trained after preprocessing numeric and categorical features.

Why included:
- easy to interpret
- stable baseline
- useful for checking whether the dataset contains meaningful linear signal

### 5.3 Random Forest Regressor
A tree-based ensemble model was trained to capture possible non-linear relationships.

Why included:
- can model interactions and non-linear patterns
- strong baseline for structured tabular data

---

## 6. Evaluation Metrics

The following metrics were used:

- **MAE**: Mean Absolute Error
- **RMSE**: Root Mean Squared Error
- **MAPE**: Mean Absolute Percentage Error

These metrics were evaluated on both the validation set and the test set.

---

## 7. Model Performance

| Model | Valid MAE | Valid RMSE | Valid MAPE | Test MAE | Test RMSE | Test MAPE |
|---|---:|---:|---:|---:|---:|---:|
| Random Forest | 35.5778 | 66.0636 | 1.9315 | 109.1443 | 194.9187 | 4.8945 |
| Linear Regression | 44.3132 | 56.5661 | 3.4323 | 63.7382 | 84.5206 | 4.4156 |
| Naive lag-1 | 138.1998 | 180.9771 | 8.7417 | 227.5230 | 323.7055 | 11.0914 |

---

## 8. Key Findings

### 8.1 Both supervised models clearly outperformed the naive baseline
This confirms that the Step_06 training dataset contains useful predictive signal beyond simply using yesterday¡¦s generation.

In other words:

- the ML feature layer is working
- lag features and sunshine-related features add value
- the project has successfully moved beyond a rule-only baseline

### 8.2 Random Forest performed best on the validation set
Random Forest achieved the lowest validation MAE, suggesting that non-linear relationships may exist in the current dataset.

However, this did not fully generalize to the test set.

### 8.3 Linear Regression was more stable on the test set
Although Linear Regression was not the best model on validation data, it achieved a much better test MAE than Random Forest.

This suggests that:

- the current dataset is still relatively small
- the simulated target may contain simplified structure
- Random Forest may already be capturing patterns that do not generalize well

So from a deployment-readiness perspective, **Linear Regression is currently the more stable baseline model**.

---

## 9. First-Round Model Selection Decision

### Selected baseline for current project narrative
**Linear Regression**

### Reason
Because this project prioritizes:

- interpretability
- stable generalization
- portfolio readability
- realistic business storytelling

Although Random Forest looked stronger on validation data, Linear Regression performed better on the final test period and is therefore a safer first-round baseline.

### Positioning of Random Forest
Random Forest is still valuable as:

- a non-linear comparison model
- a signal that richer feature engineering may improve future performance
- a candidate for later tuning after feature quality improves

---

## 10. Current Limitations

### 10.1 Target label is still simulated
The current target is not yet real measured inverter output.  
It is a simulated actual generation value derived from rule-based estimation logic.

This means current evaluation results are useful for validating the ML pipeline, but they do not yet represent full business realism.

### 10.2 Some features are entirely missing in v1
The following fields currently contain no observed values during training:

- `sunshine_rate_pct`
- `solar_radiation_mj_m2`
- `pop_value`
- `radiation_x_area`
- `pop_x_sunshine`

These features were skipped by the imputer during training.

This does not block the baseline workflow, but it indicates that weather/radiation enrichment is still incomplete.

### 10.3 Dataset size is still limited
The current dataset contains 372 rows, which is sufficient for a first baseline, but still small for more complex models.

---

## 11. Business Interpretation

From a business perspective, this step demonstrates that the project can now support a basic forecasting workflow:

- predict next-day or short-horizon site generation
- compare model-based prediction vs naive baseline
- identify whether historical generation and sunshine features improve forecast quality
- prepare for later dispatch / maintenance decision support

This is the first version of turning the PVESPS platform from a data pipeline project into a machine learning forecasting project.

---

## 12. Next Steps

### Step_07.1
Refine the baseline training pipeline:

- remove fully empty features automatically
- add feature coverage report
- compare simplified feature subsets

### Step_07.2
Improve feature quality:

- aggregate weather forecast into daily features
- enrich sunshine/radiation coverage
- review simulation rules to increase label diversity

### Step_08
Move toward batch inference:

- score daily predictions
- write results back to prediction tables
- expose outputs in dashboard / monitoring layer

### Long-term
Replace simulated labels with real measured generation data to improve realism and business value.

---

## 13. Conclusion

Step_07 successfully validated that the PVESPS training dataset can support a meaningful regression pipeline.

Main conclusion:

- supervised models clearly outperform the naive baseline
- Random Forest shows strong validation performance
- Linear Regression provides better test-set stability
- therefore, Linear Regression is selected as the first-round baseline model

This result supports the next phase of the project: moving from training dataset construction to production-oriented forecasting workflow design.