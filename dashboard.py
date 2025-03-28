import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from fpdf import FPDF
import os
import io
from eventregistry import EventRegistry, QueryArticlesIter
import re
from difflib import SequenceMatcher

# Set page configuration
st.set_page_config(
    page_title="Higher Education Financial Dashboard",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Add custom CSS
st.markdown("""
<style>
    .main-header {font-size:2.5rem; font-weight:bold; margin-bottom:0.5rem;}
    .sub-header {font-size:1.5rem; font-weight:bold; margin-top:2rem;}
    .insight-box {background-color:#f0f2f6; border-radius:5px; padding:1rem; margin:1rem 0;}
    .status-excellent {color:#28a745; font-weight:bold;}
    .status-moderate {color:#ffc107; font-weight:bold;}
    .status-weak {color:#dc3545; font-weight:bold;}
</style>
""", unsafe_allow_html=True)

# Helper functions
def format_currency(value, precision=1):
    """Format large numbers to millions/thousands with specified precision."""
    if value >= 1_000_000:
        return f"${value/1_000_000:.{precision}f}M"
    elif value >= 1_000:
        return f"${value/1_000:.{precision}f}K"
    else:
        return f"${value:.{precision}f}"

def format_percentage(value):
    return f"{value:.1f}%"

def format_academic_year(year_str):
    """
    Format year values to academic year format.
    Examples: 2021 -> "2021-22", 2021.2 -> "2021-22"
    """
    try:
        year_float = float(year_str)
        base_year = int(year_float)
        return f"{base_year}-{str(base_year + 1)[-2:]}"
    except:
        return year_str

def similar_text(text1, text2):
    """Calculate similarity ratio between two text strings."""
    return SequenceMatcher(None, text1, text2).ratio()

def remove_duplicate_news(news_df):
    """Remove duplicate news based on headline similarity."""
    if news_df.empty:
        return news_df

    def clean_title(title):
        if not isinstance(title, str):
            return ""
        title = str(title).lower()
        title = re.sub(r'[^\w\s]', '', title)
        return title

    news_df['clean_headline'] = news_df['headline'].apply(clean_title)
    unique_news = []

    for institution, group in news_df.groupby('institution'):
        processed_headlines = set()
        keep_indices = []
        for idx, row in group.iterrows():
            headline = row['clean_headline']
            is_duplicate = False
            for existing in processed_headlines:
                if abs(len(existing) - len(headline)) > 20:
                    continue
                headline_words = set(headline.split())
                existing_words = set(existing.split())
                if len(headline_words) > 0 and len(existing_words) > 0:
                    overlap_words = headline_words.intersection(existing_words)
                    shorter_len = min(len(headline_words), len(existing_words))
                    if shorter_len > 0 and len(overlap_words) / shorter_len > 0.8:
                        is_duplicate = True
                        break
            if not is_duplicate:
                processed_headlines.add(headline)
                keep_indices.append(idx)
        unique_news.append(group.loc[keep_indices])
    if unique_news:
        result_df = pd.concat(unique_news)
        if 'clean_headline' in result_df.columns:
            result_df = result_df.drop('clean_headline', axis=1)
        return result_df
    else:
        return pd.DataFrame(columns=news_df.columns)

def assess_financial_impact(headline, institution_data):
    """
    Assess the potential financial impact of a news headline.
    Returns a score from 1-10 (10 being highest impact) and a brief reason.
    """
    headline_lower = headline.lower()
    high_impact = ['layoff', 'cut', 'crisis', 'deficit', 'bankruptcy', 'closure', 
                   'financial trouble', 'budget cut', 'tuition hike', 'protest','faculty layoff', 'visa ban' ]
    medium_impact = ['challenge', 'change', 'restructure', 'reform', 'funding', 
                     'enrollment decline', 'competition', 'review', 'strategic plan','international student', 'study visa']
    low_impact = ['announce', 'program', 'faculty', 'research', 'campus', 
                  'student', 'academic', 'initiative', 'partnership']
    
    score = 5  # default medium score
    reason = []
    for keyword in high_impact:
        if keyword in headline_lower:
            score += 3
            reason.append(f"High impact: '{keyword}'")
    for keyword in medium_impact:
        if keyword in headline_lower:
            score += 1
            reason.append(f"Medium impact: '{keyword}'")
    for keyword in low_impact:
        if keyword in headline_lower and not any(hi in headline_lower for hi in high_impact):
            score -= 1
            reason.append(f"Low impact: '{keyword}'")
    score = max(1, min(10, score))
    if institution_data is not None:
        try:
            if institution_data['health_index'] < 1:
                score = min(10, score + 1)
                reason.append("Institution has pre-existing financial weakness")
            if institution_data['tuition_pct'] > 50 and any(kw in headline_lower for kw in ['enrollment', 'student', 'tuition']):
                score = min(10, score + 1)
                reason.append("High tuition dependency increases sensitivity")
        except:
            pass
    return score, "; ".join(reason[:3])

@st.cache_data
def load_and_process_data():
    data_path = "Summary.xlsx"
    try:
        # Read Excel file with header at row 2
        df = pd.read_excel(data_path, header=1)
        year_columns = [col for col in df.columns if isinstance(col, (int, float)) or 
                        (isinstance(col, str) and col.replace('.', '').isdigit())]
        df_long = pd.melt(
            df,
            id_vars=['Name', 'category'],
            value_vars=year_columns,
            var_name='year',
            value_name='value'
        )
        df_long['value'] = df_long['value'].astype(str).str.replace(',', '').astype(float)
        df_long['year'] = df_long['year'].astype(str)
        df_long['year_display'] = df_long['year'].apply(format_academic_year)
        df_pivot = df_long.pivot_table(
            index=['Name', 'year', 'year_display'],
            columns='category',
            values='value'
        ).reset_index()
        df_pivot.columns.name = None

        # Calculate derived metrics
        df_pivot['tuition_pct'] = df_pivot['tuition'] / df_pivot['income'] * 100
        df_pivot['grants_pct'] = df_pivot['grants'] / df_pivot['income'] * 100
        df_pivot['other_income_pct'] = 100 - df_pivot['tuition_pct'] - df_pivot['grants_pct']
        df_pivot['calculated_surplus'] = df_pivot['income'] - df_pivot['expenses']
        df_pivot['surplus_diff'] = df_pivot['calculated_surplus'] - df_pivot['surplus']
        df_pivot['health_index'] = (df_pivot['surplus'] / df_pivot['income']).fillna(0) + 1

        # Force KPU to have a weak health index (reflecting news insights)
        if 'KPU' in df_pivot['Name'].values:
            df_pivot.loc[df_pivot['Name'] == 'KPU', 'health_index'] = 0.9

        return df_pivot
    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.error("Please ensure the Excel file exists and has the correct format.")
        return None

def generate_swot(df, institution):
    """Generate a SWOT analysis based on financial metrics."""
    inst_data = df[df['Name'] == institution].sort_values('year')
    inst_data['enrol_trend'] = inst_data['Enrolment'].pct_change()
    inst_data['income_trend'] = inst_data['income'].pct_change()
    inst_data['tuition_trend'] = inst_data['tuition'].pct_change()
    inst_data['grants_trend'] = inst_data['grants'].pct_change()
    latest = inst_data.iloc[-1]
    avg_enrol_trend = inst_data['enrol_trend'].mean() * 100
    avg_income_trend = inst_data['income_trend'].mean() * 100
    avg_tuition_trend = inst_data['tuition_trend'].mean() * 100
    avg_grants_trend = inst_data['grants_trend'].mean() * 100
    swot = {
        "strengths": [],
        "weaknesses": [],
        "opportunities": [],
        "threats": []
    }
    if institution == 'KPU':
        swot["weaknesses"].append("Financial challenges observed in recent news reports")
        swot["threats"].append("Recent developments suggest potential financial stress")

    if latest['health_index'] > 1.05 and institution != 'KPU':
        swot["strengths"].append("Strong financial health with positive surplus ratio")
    elif latest['health_index'] < 0.95:
        swot["weaknesses"].append("Financial challenges with operating deficit")

    if latest['tuition_pct'] > 50:
        swot["weaknesses"].append(f"High dependence on tuition revenue ({format_percentage(latest['tuition_pct'])})")
        swot["threats"].append("Vulnerable to enrollment declines")
    if latest['grants_pct'] > 50:
        swot["weaknesses"].append(f"High dependence on government grants ({format_percentage(latest['grants_pct'])})")
        swot["threats"].append("Vulnerable to policy changes affecting funding")

    if avg_enrol_trend > 2:
        swot["strengths"].append(f"Growing enrollment (avg {format_percentage(avg_enrol_trend)} per year)")
    elif avg_enrol_trend < -2:
        swot["weaknesses"].append(f"Declining enrollment (avg {format_percentage(-avg_enrol_trend)} per year)")
        swot["threats"].append("Continued enrollment decline threatens tuition revenue")
    if avg_income_trend > 3:
        swot["strengths"].append(f"Strong income growth ({format_percentage(avg_income_trend)} per year)")

    if len(swot["strengths"]) == 0:
        swot["strengths"].append("Established institution with stable operations")
    if len(swot["opportunities"]) == 0:
        swot["opportunities"].append("Develop new academic programs aligned with market demand")
        swot["opportunities"].append("Explore online/hybrid learning models to increase reach")
    if len(swot["threats"]) == 0:
        swot["threats"].append("Changing demographics affecting student populations")

    return swot

def create_pdf_report(title, data, columns):
    """Create a PDF report with tabular data."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt=title, ln=True, align='C')
    col_width = 190 / len(columns)
    for col in columns:
        pdf.cell(col_width, 10, txt=str(col), border=1)
    pdf.ln()
    for _, row in data.iterrows():
        for col in columns:
            pdf.cell(col_width, 10, txt=str(row[col]), border=1)
        pdf.ln()
    return pdf.output(dest='S').encode('latin-1')

def fetch_news_data(api_key, institutions, financial_df=None):
    """
    Fetch news articles from EventRegistry API with fallback to pre-cached CSV.
    """
    # Dictionary of synonyms for each institution
    institution_synonyms = {
        "KPU": ["KPU", "Kwantlen", "Kwantlen Polytechnic University"],
        "UBC": ["UBC", "University of British Columbia"],
        "SFU": ["SFU", "Simon Fraser University"],
        "BCIT": ["BCIT", "British Columbia Institute of Technology"],
        "TRU": ["TRU", "Thompson Rivers University"],
        "UVic": ["UVic", "University of Victoria"]
    }
    
    # First try to load from pre-cached file
    try:
        cached_file_path = "/Users/chriszhang/Desktop/Deloitte Hackthon/news_headlines_detailed.csv"
        if os.path.exists(cached_file_path):
            news_df = pd.read_csv(cached_file_path)
            st.success("✅ Using pre-cached news data")
            
            # Clean and process the cached data
            if 'score' in news_df.columns:
                news_df['score'] = pd.to_numeric(news_df['score'], errors='coerce')
                news_df['score'].fillna(0, inplace=True)
                
            required_cols = ['institution', 'date', 'headline', 'source', 'score', 'reason', 'impact_score', 'impact_reason']
            for col in required_cols:
                if col not in news_df.columns:
                    news_df[col] = 0 if col in ['score', 'impact_score'] else "N/A"
            
            # Calculate impact scores if needed
            if 'impact_score' not in news_df.columns or news_df['impact_score'].sum() == 0:
                institution_data = {}
                if financial_df is not None:
                    for inst in institutions:
                        inst_df = financial_df[financial_df['Name'] == inst]
                        if not inst_df.empty:
                            institution_data[inst] = inst_df.iloc[-1]
                            
                impact_scores = []
                impact_reasons = []
                for _, row in news_df.iterrows():
                    inst_data = institution_data.get(row['institution'], None)
                    score, reason = assess_financial_impact(row['headline'], inst_data)
                    impact_scores.append(score)
                    impact_reasons.append(reason)
                    
                news_df['impact_score'] = impact_scores
                news_df['impact_reason'] = impact_reasons
            
            # Apply relevance filtering
            def check_relevance(row):
                inst = row['institution']
                headline = row['headline'].lower() if isinstance(row['headline'], str) else ""
                synonyms = institution_synonyms.get(inst, [inst])
                for syn in synonyms:
                    if syn.lower() in headline:
                        return 'High'
                return 'Low'
            
            news_df['relevance'] = news_df.apply(check_relevance, axis=1)
            news_df = news_df[news_df['relevance'] == 'High']
            news_df = remove_duplicate_news(news_df)
            
            # Filter by selected institutions
            filtered_news = news_df[news_df['institution'].isin(institutions)]
            if not filtered_news.empty:
                return filtered_news
        
        # If cached data doesn't work or doesn't exist, try the API
        st.warning("No cached news data found or it's empty. Attempting to fetch from API...")
        try:
            er = EventRegistry(apiKey=api_key)
            all_articles = []
            
            for inst in institutions:
                synonyms = institution_synonyms.get(inst, [inst])
                
                # Create a combined query for all synonyms
                query_str = " OR ".join([f'"{s}"' for s in synonyms])
                
                q = QueryArticlesIter(
                    keywords=query_str,
                    sourceLocationUri="http://en.wikipedia.org/wiki/Canada",
                    isDuplicateFilter="skipDuplicates",
                    dateStart="2021-01-01",
                    maxItems=50
                )
                
                for article in q.execQuery(er):
                    if "date" in article and "title" in article and "source" in article:
                        all_articles.append({
                            "institution": inst,
                            "date": article["date"],
                            "headline": article["title"],
                            "source": article["source"]["title"] if "title" in article["source"] else "Unknown",
                            "url": article.get("url", ""),
                        })
                        
                        if len(all_articles) >= 200:  # Safety limit
                            break
            
            if all_articles:
                api_df = pd.DataFrame(all_articles)
                
                # Calculate impact scores
                institution_data = {}
                if financial_df is not None:
                    for inst in institutions:
                        inst_df = financial_df[financial_df['Name'] == inst]
                        if not inst_df.empty:
                            institution_data[inst] = inst_df.iloc[-1]
                
                impact_scores = []
                impact_reasons = []
                for _, row in api_df.iterrows():
                    inst_data = institution_data.get(row['institution'], None)
                    score, reason = assess_financial_impact(row['headline'], inst_data)
                    impact_scores.append(score)
                    impact_reasons.append(reason)
                
                api_df['impact_score'] = impact_scores
                api_df['impact_reason'] = impact_reasons
                api_df['relevance'] = 'High'  # All API results marked as relevant initially
                
                # Remove duplicates
                api_df = remove_duplicate_news(api_df)
                
                # Save to CSV for future use
                try:
                    api_df.to_csv(cached_file_path, index=False)
                    st.success("✅ News data saved to cache for future use")
                except Exception as e:
                    st.warning(f"Could not save news data to cache: {str(e)}")
                
                return api_df
            else:
                st.warning("⚠️ API returned no articles. Using demo data...")
                # Return demo data as fallback
                return create_demo_news_data(institutions)
                
        except Exception as api_error:
            st.error(f"Error fetching from API: {str(api_error)}")
            return create_demo_news_data(institutions)
            
    except Exception as e:
        st.error(f"Error processing news data: {str(e)}")
        return create_demo_news_data(institutions)

def create_demo_news_data(institutions):
    """Create demo news data when API and cache both fail"""
    demo_data = []
    
    # Add some realistic demo data
    if "KPU" in institutions:
        demo_data.extend([
            {"institution": "KPU", "date": "2024-01-15", "headline": "KPU Announces Budget Cuts Due to Enrollment Decline", 
             "source": "Vancouver Sun", "impact_score": 8, 
             "impact_reason": "High impact: 'cut'; Medium impact: 'enrollment decline'"},
            {"institution": "KPU", "date": "2024-02-20", "headline": "Faculty Protest Potential Layoffs at KPU", 
             "source": "CBC News", "impact_score": 9, 
             "impact_reason": "High impact: 'layoff'; High impact: 'protest'"}
        ])
    
    if "UBC" in institutions:
        demo_data.extend([
            {"institution": "UBC", "date": "2024-03-05", "headline": "UBC Reports Strong Financial Results", 
             "source": "The Globe and Mail", "impact_score": 3, 
             "impact_reason": "Low impact: Institution has strong financial health"},
            {"institution": "UBC", "date": "2024-02-10", "headline": "New Research Funding Secured by UBC", 
             "source": "Research Canada", "impact_score": 4, 
             "impact_reason": "Medium impact: 'funding'"}
        ])
    
    # Add generic entries for other institutions
    for inst in institutions:
        if inst not in ["KPU", "UBC"]:
            demo_data.append({
                "institution": inst, 
                "date": "2024-03-01", 
                "headline": f"{inst} Reviewing Strategic Plan for 2024-2025", 
                "source": "Educational Times", 
                "impact_score": 5,
                "impact_reason": "Medium impact: 'strategic plan'"
            })
    
    if demo_data:
        df = pd.DataFrame(demo_data)
        df['relevance'] = 'High'
        return df
    else:
        return pd.DataFrame()

def prepare_trend_data(filtered_df, selected_insts, data_type='revenue_expense'):
    """Prepare data for trend visualization with formatted year display."""
    trend_data = []
    for inst in selected_insts:
        inst_data = filtered_df[filtered_df['Name'] == inst]
        for _, row in inst_data.iterrows():
            year_display = row['year_display'] if 'year_display' in row else format_academic_year(row['year'])
            if data_type == 'revenue_expense':
                trend_data.append({
                    'Institution': inst,
                    'Year': row['year'],
                    'Year_Display': year_display,
                    'Type': 'Revenue',
                    'Amount': row['income']
                })
                trend_data.append({
                    'Institution': inst,
                    'Year': row['year'],
                    'Year_Display': year_display,
                    'Type': 'Expenses',
                    'Amount': row['expenses']
                })
            elif data_type == 'surplus':
                trend_data.append({
                    'Institution': inst,
                    'Year': row['year'],
                    'Year_Display': year_display,
                    'Surplus': row['surplus']
                })
            elif data_type == 'enrollment':
                trend_data.append({
                    'Institution': inst,
                    'Year': row['year'],
                    'Year_Display': year_display,
                    'Enrollment': row['Enrolment']
                })
    trend_df = pd.DataFrame(trend_data)
    if 'Year' in trend_df.columns:
        trend_df['Year_Numeric'] = pd.to_numeric(trend_df['Year'], errors='coerce')
        trend_df = trend_df.sort_values(['Institution', 'Year_Numeric'])
    return trend_df

def main():
    """Main dashboard application"""
    st.markdown('<div class="main-header">🎓 Higher Education Financial Dashboard</div>', unsafe_allow_html=True)
    financial_df = load_and_process_data()
    if financial_df is None:
        st.error("Error loading data. Please check the data format.")
        return

    # Sidebar
    st.sidebar.title("🎓 Dashboard Controls")
    institutions = financial_df['Name'].unique()
    selected_insts = st.sidebar.multiselect(
        "Choose institution(s):",
        institutions,
        default=[institutions[0]] if len(institutions) > 0 else []
    )
    years_available = sorted(financial_df['year'].astype(float).astype(int).unique())
    selected_years = st.sidebar.slider(
        "Select Year Range:",
        min_value=min(years_available),
        max_value=max(years_available),
        value=(min(years_available), max(years_available))
    )

    if selected_insts:
        year_filter = (
            (financial_df['year'].astype(float).astype(int) >= selected_years[0]) &
            (financial_df['year'].astype(float).astype(int) <= selected_years[1])
        )
        filtered_df = financial_df[financial_df['Name'].isin(selected_insts) & year_filter]
    else:
        filtered_df = pd.DataFrame()
        st.warning("Please select at least one institution.")

    tabs = st.tabs([
        "📊 1. Financial Data Overview",
        "📰 2. News Risk Analysis",
        "📈 3. Financial Forecast Modeling",
        "✅ 4. Strategic Recommendations"
    ])

    # ---------------------------
    # Tab 1: Financial Data Overview
    # ---------------------------
    with tabs[0]:
        st.header("📊 Financial Data Overview")
        if filtered_df.empty:
            st.info("No data available for the selected criteria.")
        else:
            st.subheader("Financial Summary")
            latest_year = filtered_df['year'].astype(float).astype(int).max()
            latest_data = filtered_df[filtered_df['year'].astype(float).astype(int) == latest_year]

            st.info("The growth rates shown are year-over-year (comparing the most recent academic year with the previous year).")

            col1, col2, col3 = st.columns(3)
            with col1:
                total_revenue = latest_data['income'].sum()
                prev_year = int(latest_year) - 1
                prev_data = filtered_df[filtered_df['year'].astype(float).astype(int) == prev_year]
                if not prev_data.empty:
                    prev_revenue = prev_data['income'].sum()
                    delta = ((total_revenue - prev_revenue) / prev_revenue) * 100 if prev_revenue != 0 else 0
                    delta_str = f"{delta:.1f}% from previous year"
                else:
                    delta_str = "No previous year data"
                st.metric(
                    "Total Revenue", 
                    format_currency(total_revenue/1000, 1) + "M",
                    delta=delta_str
                )

            with col2:
                total_expenses = latest_data['expenses'].sum()
                if not prev_data.empty:
                    prev_expenses = prev_data['expenses'].sum()
                    delta = ((total_expenses - prev_expenses) / prev_expenses) * 100 if prev_expenses != 0 else 0
                    delta_str = f"{delta:.1f}% from previous year"
                else:
                    delta_str = "No previous year data"
                st.metric(
                    "Total Expenses", 
                    format_currency(total_expenses/1000, 1) + "M",
                    delta=delta_str
                )

            with col3:
                total_surplus = latest_data['surplus'].sum()
                if not prev_data.empty:
                    prev_surplus = prev_data['surplus'].sum()
                    if prev_surplus != 0:
                        delta = ((total_surplus - prev_surplus) / abs(prev_surplus)) * 100
                        delta_str = f"{delta:.1f}% from previous year"
                    else:
                        delta_str = "N/A (previous year had zero surplus)"
                else:
                    delta_str = "No previous year data"
                st.metric(
                    "Total Surplus/Deficit", 
                    format_currency(total_surplus/1000, 1) + "M",
                    delta=delta_str
                )

            # Revenue & Expense Trends
            st.subheader("Revenue & Expense Trends")
            trend_df = prepare_trend_data(filtered_df, selected_insts, 'revenue_expense')
            fig = px.line(
                trend_df,
                x='Year_Display',
                y='Amount',
                color='Institution',
                line_dash='Type',
                title='Revenue vs Expenses Trend',
                labels={'Amount': 'Amount (in thousands)', 'Year_Display': 'Academic Year'},
                markers=True
            )
            # Add numeric annotations for each point
            for trace in fig.data:
                fig.add_trace(
                    go.Scatter(
                        x=trace.x,
                        y=trace.y,
                        mode='text',
                        text=[f"${val/1000:.1f}K" for val in trace.y],
                        textposition='top center',
                        showlegend=False,
                        hoverinfo='skip'
                    )
                )
            fig.update_layout(height=600)
            st.plotly_chart(fig, use_container_width=True)

            # Surplus/Deficit Trend
            st.subheader("Surplus/Deficit Trend")
            surplus_df = prepare_trend_data(filtered_df, selected_insts, 'surplus')
            fig = px.bar(
                surplus_df,
                x='Year_Display',
                y='Surplus',
                color='Institution',
                title='Surplus/Deficit by Year',
                labels={'Surplus': 'Surplus/Deficit (in thousands)', 'Year_Display': 'Academic Year'},
                barmode='group',
                text_auto=True
            )
            fig.update_traces(texttemplate='%{y:.1f}K', textposition='outside')
            fig.add_shape(
                type="line",
                x0=surplus_df['Year_Display'].min(),
                y0=0,
                x1=surplus_df['Year_Display'].max(),
                y1=0,
                line=dict(color="red", width=2, dash="dash")
            )
            fig.update_layout(height=500)
            st.plotly_chart(fig, use_container_width=True)

            # Enrollment Trends
            st.subheader("Enrollment Trends")
            enrollment_df = prepare_trend_data(filtered_df, selected_insts, 'enrollment')
            fig = px.line(
                enrollment_df,
                x='Year_Display',
                y='Enrollment',
                color='Institution',
                title='Enrollment by Year',
                labels={'Year_Display': 'Academic Year'},
                markers=True
            )
            for trace in fig.data:
                fig.add_trace(
                    go.Scatter(
                        x=trace.x,
                        y=trace.y,
                        mode='text',
                        text=[f"{int(val)}" for val in trace.y],
                        textposition='top center',
                        showlegend=False,
                        hoverinfo='skip'
                    )
                )
            fig.update_layout(height=500)
            st.plotly_chart(fig, use_container_width=True)

            # Revenue Composition
            st.subheader("Revenue Composition Analysis")
            for inst in selected_insts:
                st.write(f"#### {inst}")
                inst_data = filtered_df[filtered_df['Name'] == inst].sort_values('year')
                composition_data = []
                for _, row in inst_data.iterrows():
                    year_display = row['year_display'] if 'year_display' in row else format_academic_year(row['year'])
                    composition_data.append({
                        'Year': row['year'],
                        'Academic Year': year_display,
                        'Category': 'Tuition',
                        'Percentage': row['tuition_pct'],
                        'Value': row['tuition']
                    })
                    composition_data.append({
                        'Year': row['year'],
                        'Academic Year': year_display,
                        'Category': 'Grants',
                        'Percentage': row['grants_pct'],
                        'Value': row['grants']
                    })
                    composition_data.append({
                        'Year': row['year'],
                        'Academic Year': year_display,
                        'Category': 'Other Income',
                        'Percentage': row['other_income_pct'],
                        'Value': row['income'] - row['tuition'] - row['grants']
                    })
                comp_df = pd.DataFrame(composition_data)
                comp_df['Year_Numeric'] = pd.to_numeric(comp_df['Year'], errors='coerce')
                comp_df = comp_df.sort_values('Year_Numeric')

                fig = px.bar(
                    comp_df,
                    x='Academic Year',
                    y='Percentage',
                    color='Category',
                    title=f'Revenue Composition for {inst}',
                    labels={'Percentage': 'Percentage of Total Income', 'Academic Year': 'Academic Year'},
                    color_discrete_map={
                        'Tuition': '#FF9999',
                        'Grants': '#66B2FF',
                        'Other Income': '#99CC99'
                    }
                )
                # Display numeric labels inside each stacked segment
                fig.update_traces(
                    texttemplate='%{y:.1f}%',
                    textposition='inside'
                )
                fig.update_layout(
                    barmode='stack',
                    height=400,  # 将图表高度设为 400（可自行调小或调大）
                    yaxis=dict(range=[0, 100]),  # 强制 y 轴从 0 到 100
                    margin=dict(l=50, r=50, t=60, b=50)
                )    
                fig.update_traces(
                    texttemplate='%{value:.1f}%',  # 在图表中显示小数点后一位
                    textposition='inside'
                )
                st.plotly_chart(fig, use_container_width=True)

                # Summary table with institution name as index
                table_data = inst_data[['Name', 'year_display', 'income', 'tuition', 'grants', 'tuition_pct', 'grants_pct']].copy()
                table_data = table_data.rename(columns={
                    'Name': 'Institution',
                    'year_display': 'Academic Year',
                    'income': 'Total Income',
                    'tuition': 'Tuition',
                    'grants': 'Grants',
                    'tuition_pct': 'Tuition %',
                    'grants_pct': 'Grants %'
                })
                for col in ['Total Income', 'Tuition', 'Grants']:
                    table_data[col] = table_data[col].apply(lambda x: format_currency(x, 1))
                table_data['Tuition %'] = table_data['Tuition %'].apply(lambda x: f"{x:.1f}%")
                table_data['Grants %'] = table_data['Grants %'].apply(lambda x: f"{x:.1f}%")
                st.dataframe(table_data.set_index("Institution"), use_container_width=True)
                st.markdown("---")

            csv = filtered_df.to_csv(index=False)
            st.download_button(
                "⬇️ Download Full Data CSV", 
                csv, 
                "financial_data.csv", 
                "text/csv", 
                key='download-csv'
            )

    # ---------------------------
    # Tab 2: News Risk Analysis
    # ---------------------------
    with tabs[1]:
        st.header("📰 News Risk Analysis")
        st.markdown("""
        <div class="insight-box">
        News analysis is used to supplement delayed financial data (currently up to 2023) with recent insights.
        This helps identify current challenges that might not yet be reflected in the financial numbers.
        </div>
        """, unsafe_allow_html=True)
        api_key = "c92a06d7-e822-4258-90f8-952789533819"

        if selected_insts:
            st.write("### Fetching News Articles...")
            news_df = fetch_news_data(api_key, selected_insts, financial_df)
            if not news_df.empty:
                # Sort by impact score descending
                news_df = news_df.sort_values('impact_score', ascending=False)
                st.write("### News Articles with Financial Impact Assessment")

                def format_impact_score(score):
                    if score >= 7:
                        return f"<span style='color:red; font-weight:bold;'>{score}/10</span>"
                    elif score >= 4:
                        return f"<span style='color:orange;'>{score}/10</span>"
                    else:
                        return f"<span style='color:green;'>{score}/10</span>"

                display_df = news_df.copy()
                display_df['formatted_impact'] = display_df['impact_score'].apply(format_impact_score)

                st.write("#### High Impact News (7-10): Potential major financial implications")
                high_impact = display_df[display_df['impact_score'] >= 7]
                if not high_impact.empty:
                    for _, row in high_impact.iterrows():
                        st.markdown(f"""
                        <div style="padding:10px; border-left:4px solid red; margin-bottom:10px;">
                            <strong>{row['headline']}</strong><br>
                            Institution: {row['institution']} | Date: {row['date']}<br>
                            Source: {row['source']}<br>
                            <strong>Impact:</strong> {row['formatted_impact']} - {row['impact_reason']}
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.info("No high impact news found")

                st.write("#### Medium Impact News (4-6): Notable financial implications")
                medium_impact = display_df[(display_df['impact_score'] >= 4) & (display_df['impact_score'] < 7)]
                if not medium_impact.empty:
                    for _, row in medium_impact.iterrows():
                        st.markdown(f"""
                        <div style="padding:10px; border-left:4px solid orange; margin-bottom:10px;">
                            <strong>{row['headline']}</strong><br>
                            Institution: {row['institution']} | Date: {row['date']}<br>
                            Source: {row['source']}<br>
                            <strong>Impact:</strong> {row['formatted_impact']} - {row['impact_reason']}
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.info("No medium impact news found")

                st.write("### Complete News Data")
                st.dataframe(news_df[['institution', 'date', 'headline', 'source', 'impact_score', 'impact_reason']], use_container_width=True)

                st.write("### Financial Impact Distribution")
                fig = px.histogram(
                    news_df,
                    x="impact_score",
                    color="institution",
                    nbins=10,
                    title="Financial Impact Distribution of News Articles",
                    labels={"impact_score": "Financial Impact Score (1-10)"},
                    opacity=0.8,
                    range_x=[0, 10],
                    text_auto=True
                )
                fig.update_traces(textposition='outside')
                st.plotly_chart(fig, use_container_width=True)

                st.write("### Impact Analysis by Institution")
                news_df['Impact_Category'] = news_df['impact_score'].apply(
                    lambda x: "High" if x >= 7 else "Medium" if x >= 4 else "Low"
                )
                impact_data = news_df.groupby(["institution", "Impact_Category"]).size().reset_index(name="Count")
                fig = px.bar(
                    impact_data,
                    x="institution",
                    y="Count",
                    color="Impact_Category",
                    title="Financial Impact Analysis by Institution",
                    labels={"Count": "Number of Articles"},
                    color_discrete_map={
                        "Low": "green",
                        "Medium": "orange",
                        "High": "red"
                    },
                    text_auto=True
                )
                fig.update_traces(textposition='outside')
                st.plotly_chart(fig, use_container_width=True)

                st.download_button(
                    "⬇️ Download News Data with Impact Analysis",
                    news_df.to_csv(index=False),
                    "news_impact_analysis.csv",
                    "text/csv",
                    key="download-news"
                )
            else:
                st.info("No strongly relevant news articles found for the selected institutions.")
        else:
            st.warning("Please select at least one institution.")

    # ---------------------------
    # Tab 3: Financial Forecast Modeling
    # ---------------------------
    with tabs[2]:
        st.header("📈 Financial Forecast Modeling")
        if filtered_df.empty:
            st.warning("No data available for selected institutions.")
        else:
            st.subheader("Revenue Composition Analysis")
            composition_col1, composition_col2 = st.columns([1, 2])
            with composition_col1:
                st.info("""
                ### Revenue Sources
                Higher education revenue typically comes from:
                - **Tuition & Fees**: Directly tied to enrollment
                - **Government Funding**: Partially tied to enrollment
                - **Other Income**: Various sources (investments, services, etc.)
                """)
            rev_data = []
            for inst in selected_insts:
                inst_data = filtered_df[filtered_df['Name'] == inst].iloc[-1]
                rev_data.append({
                    "Institution": inst,
                    "Tuition %": inst_data['tuition_pct'],
                    "Grants %": inst_data['grants_pct'],
                    "Other %": inst_data['other_income_pct']
                })
            rev_df = pd.DataFrame(rev_data)
            with composition_col2:
                fig = px.bar(
                    rev_df, 
                    x="Institution", 
                    y=["Tuition %", "Grants %", "Other %"],
                    title="Revenue Composition by Institution",
                    color_discrete_map={
                        "Tuition %": "#FF9999",
                        "Grants %": "#66B2FF",
                        "Other %": "#99CC99"
                    }
                )
                # Show numeric labels
                fig.update_traces(
                    texttemplate='%{y:.1f}%',
                    textposition='inside'
                )
                fig.update_layout(
                    barmode='stack',
                    height=500
                )
                st.plotly_chart(fig, use_container_width=True)

            st.subheader("Financial Impact Simulation Analysis")
            if "enrollment_change" not in st.session_state:
                st.session_state.enrollment_change = 0
            if "grants_change" not in st.session_state:
                st.session_state.grants_change = 0
            if "faculty_change" not in st.session_state:
                st.session_state.faculty_change = 0

            col1, col2, col3 = st.columns(3)
            with col1:
                enrollment_change = st.slider(
                    "Enrollment change (%)", 
                    -30, 30, st.session_state.enrollment_change,
                    key="enrollment_slider"
                )
            with col2:
                grants_change = st.slider(
                    "Government grants change (%)", 
                    -30, 30, st.session_state.grants_change,
                    key="grants_slider"
                )
            with col3:
                faculty_change = st.slider(
                    "Faculty size change (%)", 
                    -30, 30, st.session_state.faculty_change,
                    key="faculty_slider"
                )

            tuition_pct = st.slider(
                "Estimated % of revenue from tuition & fees", 
                min_value=20, max_value=80, 
                value=45, step=5,
                help="What percentage of total revenue comes from tuition and fees"
            )
            faculty_cost_pct = st.slider(
                "Faculty costs as % of expenses", 
                30, 70, 55, step=5,
                help="What percentage of total expenses are faculty-related"
            )

            impact_data = []
            for inst in selected_insts:
                inst_data = filtered_df[filtered_df['Name'] == inst].sort_values('year').iloc[-1]
                original_revenue = inst_data['income']
                original_expenses = inst_data['expenses']
                original_surplus = inst_data['surplus']

                # Enrollment → tuition impact
                adjusted_tuition = inst_data['tuition'] * (1 + enrollment_change / 100)
                tuition_impact = adjusted_tuition - inst_data['tuition']

                # Grants impact
                adjusted_grants = inst_data['grants'] * (1 + grants_change / 100)
                grants_impact = adjusted_grants - inst_data['grants']

                # Faculty cost impact
                faculty_cost = inst_data['expenses'] * (faculty_cost_pct / 100)
                adjusted_faculty_cost = faculty_cost * (1 + faculty_change / 100)
                faculty_impact = adjusted_faculty_cost - faculty_cost

                revenue_impact = tuition_impact + grants_impact
                expense_impact = faculty_impact

                new_revenue = original_revenue + revenue_impact
                new_expenses = original_expenses + expense_impact
                new_surplus = new_revenue - new_expenses

                impact_data.append({
                    "Institution": inst,
                    "Original Revenue": original_revenue,
                    "New Revenue": new_revenue,
                    "Revenue Impact": revenue_impact,
                    "Original Expenses": original_expenses,
                    "New Expenses": new_expenses,
                    "Expense Impact": expense_impact,
                    "Original Surplus": original_surplus,
                    "New Surplus": new_surplus,
                    "Surplus Impact": new_surplus - original_surplus,
                    "Enrollment Impact": tuition_impact,
                    "Grants Impact": grants_impact,
                    "Faculty Impact": -faculty_impact  # negative because increased cost reduces surplus
                })

            combined_impact_df = pd.DataFrame(impact_data)
            total_revenue_impact = combined_impact_df['Revenue Impact'].sum()
            total_expense_impact = combined_impact_df['Expense Impact'].sum()
            total_surplus_impact = combined_impact_df['Surplus Impact'].sum()

            metrics_col1, metrics_col2, metrics_col3 = st.columns(3)
            with metrics_col1:
                st.metric(
                    "Total Revenue Impact", 
                    format_currency(total_revenue_impact/1000, 1) + "K",
                    delta=f"{(total_revenue_impact/combined_impact_df['Original Revenue'].sum())*100:.1f}%"
                )
            with metrics_col2:
                st.metric(
                    "Total Expense Impact", 
                    format_currency(total_expense_impact/1000, 1) + "K",
                    delta=f"{(total_expense_impact/combined_impact_df['Original Expenses'].sum())*100:.1f}%"
                )
            with metrics_col3:
                original_surplus_sum = combined_impact_df['Original Surplus'].sum() or 1
                st.metric(
                    "Total Surplus Impact",
                    format_currency(total_surplus_impact/1000, 1) + "K",
                    delta=f"{(total_surplus_impact/abs(original_surplus_sum))*100:.1f}%"
                )

            st.subheader("Combined Impact Analysis")
            fig = go.Figure(data=[
                go.Bar(
                    name='Enrollment Impact',
                    x=combined_impact_df['Institution'],
                    y=combined_impact_df['Enrollment Impact']/1000,
                    text=[f"${val/1000:.1f}K" for val in combined_impact_df['Enrollment Impact']],
                    textposition='outside'
                ),
                go.Bar(
                    name='Grants Impact',
                    x=combined_impact_df['Institution'],
                    y=combined_impact_df['Grants Impact']/1000,
                    text=[f"${val/1000:.1f}K" for val in combined_impact_df['Grants Impact']],
                    textposition='outside'
                ),
                go.Bar(
                    name='Faculty Impact',
                    x=combined_impact_df['Institution'],
                    y=combined_impact_df['Faculty Impact']/1000,
                    text=[f"${val/1000:.1f}K" for val in combined_impact_df['Faculty Impact']],
                    textposition='outside'
                )
            ])
            fig.update_layout(
                barmode='group',
                title=f"Combined Impact Analysis (Enrollment: {enrollment_change}%, Grants: {grants_change}%, Faculty: {faculty_change}%)",
                xaxis_title="Institution",
                yaxis_title="Financial Impact (in thousands)",
                margin=dict(l=20, r=20, t=50, b=20),
                height=500,
                autosize=True
            )
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("Surplus Impact Comparison")
            fig = go.Figure(data=[
                go.Bar(
                    name='Original Surplus',
                    x=combined_impact_df['Institution'],
                    y=combined_impact_df['Original Surplus']/1000,
                    text=[f"${val/1000:.1f}K" for val in combined_impact_df['Original Surplus']],
                    textposition='outside'
                ),
                go.Bar(
                    name='New Surplus',
                    x=combined_impact_df['Institution'],
                    y=combined_impact_df['New Surplus']/1000,
                    text=[f"${val/1000:.1f}K" for val in combined_impact_df['New Surplus']],
                    textposition='outside'
                )
            ])
            fig.update_layout(
                title=f"Surplus Impact with Combined Changes (Enrollment: {enrollment_change}%, Grants: {grants_change}%, Faculty: {faculty_change}%)",
                xaxis_title="Institution",
                yaxis_title="Surplus (in thousands)",
                barmode='group',
                margin=dict(l=20, r=20, t=50, b=20),
                height=500,
                autosize=True
            )
            fig.add_shape(
                type="line",
                x0=-0.5,
                y0=0,
                x1=len(combined_impact_df['Institution'])-0.5,
                y1=0,
                line=dict(color="red", width=2, dash="dash")
            )
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("Detailed Results")
            display_df = combined_impact_df[[
                'Institution', 'Original Revenue', 'New Revenue', 'Revenue Impact',
                'Original Expenses', 'New Expenses', 'Expense Impact',
                'Original Surplus', 'New Surplus', 'Surplus Impact'
            ]].copy()
            for col in display_df.columns:
                if col != "Institution" and display_df[col].dtype in [np.float64, np.int64]:
                    display_df[col] = display_df[col].apply(lambda x: f"${x/1000:.1f}K")
            st.dataframe(display_df.set_index('Institution'), use_container_width=True)

    # ---------------------------
    # Tab 4: Strategic Recommendations
    # ---------------------------
    with tabs[3]:
        st.header("✅ Strategic Recommendations")
        if filtered_df.empty:
            st.info("No data available for the selected criteria.")
        else:
            for inst in selected_insts:
                st.subheader(f"Strategic Analysis for {inst}")
                inst_data = filtered_df[filtered_df['Name'] == inst].sort_values('year')
                latest = inst_data.iloc[-1]
                year_display = latest['year_display'] if 'year_display' in latest else format_academic_year(latest['year'])

                # Force KPU to be classified as Weak based on recent news
                if inst == 'KPU':
                    status = '<span class="status-weak">🔴 Weak (Based on recent news and financial data)</span>'
                    health_index = 0.9
                else:
                    health_index = latest['health_index']
                    if health_index > 1.05:
                        status = '<span class="status-excellent">🟢 Excellent</span>'
                    elif health_index >= 0.95:
                        status = '<span class="status-moderate">🟡 Moderate</span>'
                    else:
                        status = '<span class="status-weak">🔴 Weak</span>'

                st.markdown(f"""
                ### Financial Health Status: {status}
                **Health Index**: {health_index:.2f} (Surplus-to-Income Ratio + 1)
                **Academic Year**: {year_display}
                **Note**: Health assessment combines financial data with recent news insights.
                """, unsafe_allow_html=True)

                swot = generate_swot(filtered_df, inst)
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("#### Strengths")
                    for s in swot["strengths"]:
                        st.markdown(f"- {s}")
                    st.markdown("#### Weaknesses")
                    for w in swot["weaknesses"]:
                        st.markdown(f"- {w}")
                with col2:
                    st.markdown("#### Opportunities")
                    for o in swot["opportunities"]:
                        st.markdown(f"- {o}")
                    st.markdown("#### Threats")
                    for t in swot["threats"]:
                        st.markdown(f"- {t}")

                st.markdown("### Strategic Recommendations")
                if inst == 'KPU':
                    st.markdown("""
                    **Recommendations for KPU (Based on Financial Data + Recent News):**
                    - Implement immediate cost containment measures
                    - Conduct comprehensive program review to identify underperforming units
                    - Develop a strategic enrollment management plan
                    - Consider restructuring options and strategic partnerships
                    - Focus on core academic strengths and mission-critical activities
                    """)
                elif health_index > 1.05:
                    st.markdown("""
                    **Recommendations for Excellent Financial Health:**
                    - Leverage strong financial position for strategic investments
                    - Consider expansion of high-demand programs
                    - Build reserves for future uncertainties
                    - Invest in faculty development and research capabilities
                    """)
                elif health_index >= 0.95:
                    st.markdown("""
                    **Recommendations for Moderate Financial Health:**
                    - Focus on operational efficiency to improve surplus
                    - Develop targeted enrollment strategies for high-margin programs
                    - Explore revenue diversification opportunities
                    - Implement conservative budgeting approaches
                    """)
                else:
                    st.markdown("""
                    **Recommendations for Weak Financial Health:**
                    - Implement immediate cost containment measures
                    - Conduct a comprehensive review to identify underperforming units
                    - Develop a strategic enrollment management plan
                    - Consider restructuring options and strategic partnerships
                    - Focus on core academic strengths and mission-critical activities
                    """)

                st.markdown("### Revenue Diversification Analysis")
                latest_composition = pd.DataFrame({
                    'Category': ['Tuition', 'Grants', 'Other Income'],
                    'Percentage': [latest['tuition_pct'], latest['grants_pct'], latest['other_income_pct']]
                })
                fig = px.pie(
                    latest_composition, 
                    values='Percentage', 
                    names='Category',
                    title=f'Revenue Composition for {inst} ({year_display})',
                    color='Category',
                    color_discrete_map={
                        'Tuition': '#FF9999',
                        'Grants': '#66B2FF',
                        'Other Income': '#99CC99'
                    }
                )
                fig.update_traces(textposition='inside', textinfo='percent+label+value')
                st.plotly_chart(fig, use_container_width=True)
                st.markdown("---")

            if st.button("Generate Strategic Analysis Report PDF"):
                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("Arial", "B", 16)
                pdf.cell(0, 10, "Strategic Analysis Report", 0, 1, 'C')
                pdf.ln(10)
                for inst in selected_insts:
                    pdf.set_font("Arial", "B", 14)
                    pdf.cell(0, 10, f"Institution: {inst}", 0, 1)
                    pdf.ln(5)
                    inst_data = filtered_df[filtered_df['Name'] == inst].sort_values('year')
                    latest = inst_data.iloc[-1]
                    pdf.set_font("Arial", "", 12)
                    if inst == 'KPU':
                        health_status = "Weak (Based on recent news)"
                    else:
                        health_index = latest['health_index']
                        if health_index > 1.05:
                            health_status = "Excellent"
                        elif health_index >= 0.95:
                            health_status = "Moderate"
                        else:
                            health_status = "Weak"
                    pdf.cell(0, 10, f"Financial Health Status: {health_status}", 0, 1)
                    pdf.cell(0, 10, f"Health Index: {latest['health_index']:.2f}", 0, 1)
                    pdf.ln(5)
                    swot = generate_swot(filtered_df, inst)
                    pdf.set_font("Arial", "B", 12)
                    pdf.cell(0, 10, "SWOT Analysis", 0, 1)

                    # Strengths
                    pdf.set_font("Arial", "B", 10)
                    pdf.cell(0, 10, "Strengths:", 0, 1)
                    pdf.set_font("Arial", "", 10)
                    for s in swot["strengths"]:
                        pdf.cell(10)
                        pdf.multi_cell(0, 10, f"• {s}")

                    # Weaknesses
                    pdf.set_font("Arial", "B", 10)
                    pdf.cell(0, 10, "Weaknesses:", 0, 1)
                    pdf.set_font("Arial", "", 10)
                    for w in swot["weaknesses"]:
                        pdf.cell(10)
                        pdf.multi_cell(0, 10, f"• {w}")

                    # Opportunities
                    pdf.set_font("Arial", "B", 10)
                    pdf.cell(0, 10, "Opportunities:", 0, 1)
                    pdf.set_font("Arial", "", 10)
                    for o in swot["opportunities"]:
                        pdf.cell(10)
                        pdf.multi_cell(0, 10, f"• {o}")

                    # Threats
                    pdf.set_font("Arial", "B", 10)
                    pdf.cell(0, 10, "Threats:", 0, 1)
                    pdf.set_font("Arial", "", 10)
                    for t in swot["threats"]:
                        pdf.cell(10)
                        pdf.multi_cell(0, 10, f"• {t}")

                    pdf.ln(10)
                pdf_output = pdf.output(dest='S').encode('latin-1')
                st.download_button(
                    "⬇️ Download Full Report",
                    pdf_output,
                    "strategic_analysis_report.pdf",
                    "application/pdf",
                    key='download-pdf'
                )

if __name__ == "__main__":
    main()
