import streamlit as st
import pandas as pd
import requests
import plotly.express as px
import plotly.graph_objects as go

# Load financial data
financial_df = pd.read_csv("financial_summary_extracted.csv")
financial_df.dropna(inplace=True)
financial_df['revenue'] = financial_df['revenue'].replace({',': ''}, regex=True).astype(float)
financial_df['expenses'] = financial_df['expenses'].replace({',': ''}, regex=True).astype(float)
financial_df['surplus'] = financial_df['surplus'].replace({',': ''}, regex=True).astype(float)

# Load detailed news headlines
news_df = pd.read_csv("news_headlines_detailed.csv")

# Sidebar
st.sidebar.title("🎓 Institution Selector")
st.sidebar.markdown("---")
st.sidebar.subheader("🤖 Run AI News Scoring")
run_ai = st.sidebar.button("🔍 Analyze News Headlines via AI")

# Sidebar Selector
institutions = financial_df['institution'].unique()
selected_insts = st.sidebar.multiselect("Choose one or more institutions:", institutions, default=[institutions[0]])

# AI API Simulation Function
def analyze_news_sentiment(text):
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": "Bearer YOUR_OPENROUTER_API_KEY",
                "Content-Type": "application/json"
            },
            json={
                "model": "mistralai/mistral-7b-instruct",
                "messages": [
                    {"role": "system", "content": "You are a consulting AI. Score the financial risk of this headline from -10 (bad) to +10 (good). Return JSON: {score: X, reason: '...'}."},
                    {"role": "user", "content": text}
                ]
            }
        )
        content = response.json()['choices'][0]['message']['content']
        result = eval(content)
        return result['score'], result['reason']
    except:
        return 0.0, "AI error or timeout"

# Run AI if button clicked
if run_ai:
    news_df[['score', 'reason']] = news_df['headline'].apply(lambda h: pd.Series(analyze_news_sentiment(h)))

# Grouped news score
news_scores = news_df.groupby("institution")["score"].mean().reset_index()
news_scores.rename(columns={"score": "news_risk"}, inplace=True)

# Merge scores with financial data
merged_df = pd.merge(financial_df, news_scores, on="institution", how="left")
if 'news_risk' not in merged_df.columns:
    merged_df['news_risk'] = 0
else:
    merged_df['news_risk'] = merged_df['news_risk'].fillna(0)
merged_df['financial_score'] = (merged_df['surplus'] / merged_df['revenue']).clip(0, 0.15) * 100

# Add Streamlit sliders for alpha and beta weights
alpha_weight = st.sidebar.slider("⚖️ Alpha (Financial) Weight", 0.0, 1.0, 0.6, 0.05)
beta_weight = 1.0 - alpha_weight

# Compute new weighted health index
merged_df['health_index'] = (alpha_weight * merged_df['financial_score'] + beta_weight * merged_df['news_risk']).clip(0, 100)
merged_df['year'] = merged_df.get('year', financial_df['year'].max())

# Tabbed Layout with 5 stages
tabs = st.tabs(["📊 1. Original Data", "📈 2. Financial Modeling", "🤖 3. AI Suggestions", "📰 4. News Risk Signals", "✅ 5. Final Solution"])

with tabs[0]:
    st.header("📊 Stage 1: Original Data Overview")
    stage1_df = merged_df[['institution', 'year', 'revenue', 'expenses', 'surplus']]
    st.dataframe(stage1_df)
    st.download_button("⬇️ Download CSV", data=stage1_df.to_csv(index=False), file_name="stage1_original_data.csv")

with tabs[1]:
    st.header("📈 Stage 2: Financial Modeling Analysis")
    change_pct = st.slider("Enrollment change (%)", -30, 30, 0, step=1)
    combined_model_df = pd.DataFrame()

    for inst in selected_insts:
        st.markdown(f"### 🏫 {inst}")
        data = merged_df[merged_df['institution'] == inst].sort_values(by="year")
        data['Adjusted Revenue'] = data['revenue'] * (1 + change_pct / 100)
        st.metric(label="Composite Health Score", value=f"{data.iloc[-1]['health_index']:.1f}%")

        fig1 = px.line(data, x="year", y=["revenue", "Adjusted Revenue"], title="Revenue vs Adjusted Revenue")
        st.plotly_chart(fig1, use_container_width=True)

        fig2 = px.bar(data, x="year", y=["expenses", "surplus"], barmode="group", title="Yearly Surplus vs Expenses")
        st.plotly_chart(fig2, use_container_width=True)

        model_df = data[['institution', 'year', 'revenue', 'Adjusted Revenue', 'expenses', 'surplus']]
        combined_model_df = pd.concat([combined_model_df, model_df], ignore_index=True)

    st.download_button("⬇️ Download CSV", data=combined_model_df.to_csv(index=False), file_name="stage2_financial_model.csv")

with tabs[2]:
    st.header("🤖 Stage 3: AI Suggestions")
    ai_df = []
    for inst in selected_insts:
        row = merged_df[merged_df['institution'] == inst].iloc[-1]
        st.markdown(f"**{inst}** → Health Index: **{row['health_index']:.1f}%**, Status: {'🟢' if row['health_index'] > 85 else ('🟡' if row['health_index'] > 70 else ('🟠' if row['health_index'] >= 50 else '🔴'))}")
        st.markdown("- Health Index combines financial strength (α) and sentiment risk (β). Adjustable via sidebar.")
        ai_df.append({"institution": inst, "score": row['health_index']})

    ai_df = pd.DataFrame(ai_df)
    st.download_button("⬇️ Download CSV", data=ai_df.to_csv(index=False), file_name="stage3_ai_suggestions.csv")

with tabs[3]:
    st.header("📰 Stage 4: News-Based Risk Signals")
    news_trend_df = news_df[news_df['institution'].isin(selected_insts)].groupby('institution').agg(avg_score=('score', 'mean')).reset_index()
    news_trend_df['color'] = news_trend_df['avg_score'].apply(lambda x: '#2ecc71' if x > 5 else ('#f1c40f' if x > 0 else '#e74c3c'))
    trend_chart = {
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": list(news_trend_df['institution'])},
        "yAxis": {"type": "value"},
        "series": [{
            "type": "bar",
            "data": [
                {"value": v, "itemStyle": {"color": c}}
                for v, c in zip(news_trend_df['avg_score'], news_trend_df['color'])
            ]
        }]
    }
    st.subheader("📈 News Sentiment Trend by Institution")
    from streamlit_echarts import st_echarts
    st_echarts(trend_chart, height="400px")

    combined_news_df = pd.DataFrame()
    for inst in selected_insts:
        st.markdown(f"### 📰 News Headlines for {inst}")
        inst_news = news_df[news_df['institution'] == inst][['institution', 'headline', 'score', 'reason']]
        st.dataframe(inst_news.reset_index(drop=True))
        combined_news_df = pd.concat([combined_news_df, inst_news], ignore_index=True)

    st.download_button("⬇️ Download CSV", data=combined_news_df.to_csv(index=False), file_name="stage4_news_signals.csv")

with tabs[4]:
    st.header("✅ Stage 5: Final Solution Recommendation")
    solution_df = []
    for inst in selected_insts:
        row = merged_df[merged_df['institution'] == inst].iloc[-1]
        if row['health_index'] < 50:
            st.markdown(f"### {inst}: 🔴 Critical")
            st.markdown("- 📉 *Consider cost control and alternative funding models.*")
        elif row['health_index'] < 85:
            st.markdown(f"### {inst}: 🟡 Warning")
            st.markdown("- ⚠️ *Monitor enrollment and adjust spending plans.*")
        else:
            st.markdown(f"### {inst}: 🟢 Excellent")
            st.markdown("- ✅ *Maintain current trajectory. Opportunities for strategic investment.*")

        solution_df.append({"institution": inst, "score": row['health_index'], "status": ('Critical' if row['health_index'] < 50 else ('Warning' if row['health_index'] < 70 else ('Stable' if row['health_index'] < 85 else 'Excellent')) )})

    solution_df = pd.DataFrame(solution_df)
    st.download_button("⬇️ Download CSV", data=solution_df.to_csv(index=False), file_name="stage5_solutions.csv")
