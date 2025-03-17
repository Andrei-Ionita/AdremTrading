import streamlit as st
import requests
from streamlit_lottie import st_lottie

st.set_page_config(page_title="OngyAI Forecasting", layout="wide")

# Load the human-robot interaction animation
def load_lottie_url(url: str):
    r = requests.get(url)
    if r.status_code != 200:
        return None
    return r.json()

lottie_robot = load_lottie_url("https://assets8.lottiefiles.com/packages/lf20_yslfmgs5.json")

# Custom styles for the new layout
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    .main-container {
        display: flex;
        justify-content: space-between;
        padding: 50px 100px;
    }

    .sidebar-container {
        flex: 1;
        margin-right: 30px;
    }

    .animation-container {
        flex: 2;
        display: flex;
        justify-content: center;
    }

    .hero-section {
        padding: 40px;
        background: linear-gradient(135deg, #1652F0, #1D2633);
        color: white;
        text-align: center;
        border-radius: 12px;
        margin-top: 30px;
    }

    .hero-section h1 {
        font-size: 48px;
        font-weight: bold;
    }

    .hero-section p {
        font-size: 20px;
        margin-top: 10px;
    }

    .feature-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 30px;
        margin-top: 40px;
    }

    .feature-card {
        background-color: #1D2633;
        padding: 30px;
        color: white;
        border-radius: 12px;
        text-align: center;
        box-shadow: 0px 4px 8px rgba(0, 0, 0, 0.3);
        transition: transform 0.2s;
    }

    .feature-card:hover {
        transform: scale(1.05);
    }

    .footer {
        margin-top: 50px;
        text-align: center;
        color: #888;
    }
    </style>
""", unsafe_allow_html=True)

# Sidebar navigation content
def render_sidebar():
    st.markdown("""
        <div class="sidebar-container">
            <h3>Navigation</h3>
            <ul style="list-style:none; padding-left: 0;">
                <li>🏠 Home</li>
                <li>📊 Forecast</li>
                <li>📈 Market Fundamentals</li>
                <li>⚡ Balancing Market</li>
            </ul>
        </div>
    """, unsafe_allow_html=True)

# Render the home page content
def render_home_page():
    st.markdown("""
        <div class='hero-section'>
            <h1>AI-Powered Energy Forecasting</h1>
            <p>Gain real-time insights to optimize energy trading and asset management.</p>
        </div>

        <div class="feature-grid">
            <div class="feature-card">
                <h3>Forecasting</h3>
                <p>Accurate short-term and day-ahead energy production and consumption forecasts.</p>
            </div>
            <div class="feature-card">
                <h3>Market Fundamentals</h3>
                <p>Track supply-demand imbalances, renewable energy contributions, and market drivers.</p>
            </div>
            <div class="feature-card">
                <h3>Balancing Market</h3>
                <p>Analyze real-time imbalance volumes and forecast price deviations.</p>
            </div>
        </div>

        <div class="footer">
            <p>&copy; 2025 OngyAI Forecasting | Powering Energy Markets</p>
        </div>
    """, unsafe_allow_html=True)

# Main function to arrange layout
def main():

    st.markdown("""
        <div class="main-container">
            <div class="sidebar-container">
                <!-- Render sidebar here -->
                <h3>Navigation</h3>
                <ul style="list-style:none; padding-left: 0;">
                    <li>🏠 Home</li>
                    <li>📊 Forecast</li>
                    <li>📈 Market Fundamentals</li>
                    <li>⚡ Balancing Market</li>
                </ul>
            </div>
            <div class="animation-container">
                <!-- Render the robot animation here -->
                <div>
                    """, unsafe_allow_html=True)

    st_lottie(lottie_robot, height=300, key="robot_animation")

    st.markdown("""
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Render the hero section and feature grid
    render_home_page()

if __name__ == "__main__":
    main()
