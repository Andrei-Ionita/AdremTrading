import streamlit as st
import streamlit.components.v1 as stc
import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
import xlsxwriter
import base64
from streamlit_lottie import st_lottie
import requests

# Importing apps and pages
# from eda import render_eda_page
from ml import render_forecast_page, render_balancing_market_page
from fundamentals import render_fundamentals_page
from balancing import render_balancing_market_intraday_page
# from Balancing_Market_intraday_layout import render_balancing_market_intraday_page
from Transavia.transavia import render_Transavia_page

# Load Lottie animation
def load_lottie_url(url: str):
    r = requests.get(url)
    if r.status_code != 200:
        return None
    return r.json()

lottie_background = load_lottie_url("https://assets2.lottiefiles.com/packages/lf20_zrqthn6o.json")

#================================================CSS=================================

# CSS for polished design
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    .main-container {
        padding: 50px;
    }

    .hero-section {
        background: linear-gradient(135deg, #1652F0, #1D2633);
        padding: 40px;
        border-radius: 12px;
        text-align: center;
        color: white;
    }

    .hero-section h1 {
        font-size: 48px;
        font-weight: bold;
        margin-bottom: 10px;
    }

    .hero-section p {
        font-size: 20px;
        margin-bottom: 20px;
    }

    .feature-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 20px;
        margin-top: 40px;
    }

    .feature-card {
        background-color: #1D2633;
        padding: 20px;
        border-radius: 12px;
        text-align: center;
        color: white;
        box-shadow: 0px 4px 8px rgba(0, 0, 0, 0.3);
        transition: transform 0.2s;
    }

    .feature-card:hover {
        transform: scale(1.05);
    }

    .feature-card h3 {
        font-size: 24px;
        margin-bottom: 10px;
    }

    .feature-card p {
        font-size: 16px;
        margin-bottom: 15px;
    }

    .cta-button {
        background-color: #1652F0;
        padding: 10px 20px;
        border: none;
        border-radius: 8px;
        color: white;
        font-weight: bold;
        cursor: pointer;
        transition: background-color 0.3s;
    }

    .cta-button:hover {
        background-color: #0E3FBF;
    }

    .footer {
        margin-top: 50px;
        text-align: center;
        color: #888;
    }
    </style>
""", unsafe_allow_html=True)

def get_base64_encoded_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode('utf-8')

encoded_image_1 = get_base64_encoded_image("./assets/AI_pics/trading_AI.jpg")
encoded_image_2 = get_base64_encoded_image("./assets/AI_pics/ai_face3.png")
encoded_image_3 = get_base64_encoded_image("./assets/AI_pics/ai_face4.png")

# Dynamic Slideshow Section
slideshow_html = f"""
    <div class="slideshow-container">
        <img src="data:image/png;base64,{encoded_image_1}" style="width:100%; border-radius: 12px;">
    </div>
"""

def render_home_page():
    # Display the Lottie background animation
    st_lottie(lottie_background, height=200, key="background", speed=1, loop=True)

    # Hero section
    st.markdown("""
        <div class="hero-section">
            <h1>AI-Powered Energy Forecasting</h1>
            <p>Gain real-time insights to optimize energy trading and asset management.</p>
        </div>
    """, unsafe_allow_html=True)

    # Slideshow
    stc.html(slideshow_html, height=600)

    # Feature grid section
    st.markdown("""
        <div class="feature-grid">
            <div class="feature-card">
                <h3>Forecasting</h3>
                <p>Accurate short-term and day-ahead energy production and consumption forecasts.</p>
                <button class="cta-button" onclick="window.location.href='#'">Explore</button>
            </div>
            <div class="feature-card">
                <h3>Market Fundamentals</h3>
                <p>Track supply-demand imbalances, renewable energy contributions, and market drivers.</p>
                <button class="cta-button" onclick="window.location.href='#'">Learn More</button>
            </div>
            <div class="feature-card">
                <h3>Balancing Market</h3>
                <p>Analyze real-time imbalance volumes and forecast price deviations.</p>
                <button class="cta-button" onclick="window.location.href='#'">Discover</button>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Footer section
    st.markdown("""
        <div class="footer">
            <p>&copy; 2025 OngyAI Forecasting | Powering Energy Markets</p>
        </div>
    """, unsafe_allow_html=True)

# Sidebar rendering with icons and dynamic page switching
def render_sidebar():
    st.markdown("<div class='sidebar-title'>Navigation</div>", unsafe_allow_html=True)
    
    menu_options = {
        "Home": "🏠 Home",
        "Forecast": "📈 Forecast",
        "Market Fundamentals": "📊 Market Fundamentals",
        "Balancing Market": "⚡ Balancing Market",
        # "Transavia": "🚄 Transavia"
    }

    selected_page = st.radio("", list(menu_options.keys()), key="page_select", format_func=lambda x: menu_options[x])

    return selected_page

def main():
    page = render_sidebar()

    # Route pages
    if page == "Home":
        render_home_page()
    elif page == "Forecast":
        render_forecast_page()
    elif page == "Market Fundamentals":
        render_fundamentals_page()
    elif page == "Balancing Market":
        render_balancing_market_page()
        render_balancing_market_intraday_page()
    # elif page == "Transavia":
    #     render_Transavia_page()


if __name__ == "__main__":
    main()