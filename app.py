import streamlit as st
import streamlit.components.v1 as stc
import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
import xlsxwriter
import base64

# Importing apps and pages
# from eda import render_eda_page
from ml import render_forecast_page, render_balancing_market_page
from fundamentals import render_fundamentals_page
from balancing import render_balancing_market_intraday_page
# from Balancing_Market_intraday_layout import render_balancing_market_intraday_page


#================================================CSS=================================
# st.markdown(
#     """
#     <style>
#     /* Custom Netflix-inspired styles */
#     .css-18e3th9 {  /* Main background */
#         background-color: #141414;
#     }
#     .css-1d391kg {  /* Sidebar */
#         background-color: #2c2c2c;
#     }
#     .css-1v0mbdj, .css-10trblm {  /* Streamlit titles and headers */
#         color: #e50914;
#     }
#     .css-1d391kg h2 {
#         color: #e50914;
#     }
#     button {
#         background-color: #e50914 !important;
#         color: white !important;
#         border-radius: 5px;
#         border: none;
#     }
#     button:hover {
#         background-color: #f40612 !important;
#     }
#     </style>
#     """,
#     unsafe_allow_html=True
# )

# Inject custom CSS
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap');

    /* Override the default font */
    html, body, [class*="css"]  {
        font-family: 'Inter', sans-serif;
    }

    /* Clean card styles similar to Coinbase */
    .st-card {
        background-color: #ffffff;
        border-radius: 12px;
        padding: 20px;
        box-shadow: 0px 4px 8px rgba(0, 0, 0, 0.05);
        margin-bottom: 15px;
    }

    /* Buttons with futuristic Coinbase hover effect */
    .st-button>button {
        background-color: #3e8ef7;
        color: #ffffff;
        border-radius: 8px;
        padding: 10px 15px;
        font-weight: bold;
        border: none;
    }

    .st-button>button:hover {
        background-color: #2968c8;
        transition: 0.3s ease;
    }

    /* Light section dividers for contrast */
    .section-divider {
        background-color: #f3f4f6;
        height: 3px;
        margin: 20px 0;
    }
    </style>
""", unsafe_allow_html=True)

#========================================================HTML Components==========================
# Define rendering functions before the main function.
slideshow_html = """
<style>
* {box-sizing: border-box}
body {font-family: Verdana, sans-serif; margin:0}
.mySlides {display: none}
img {vertical-align: middle; max-height: 700px; width: auto; max-width: 100%;}

/* Slideshow container */
.slideshow-container {
	max-width: 100%;
	height: 700px;
	position: relative;
	margin: auto;
}

/* Next & previous buttons */
.prev, .next {
	cursor: pointer;
	position: absolute;
	top: 50%;
	width: auto;
	padding: 16px;
	margin-top: -22px;
	color: white;
	font-weight: bold;
	font-size: 18px;
	transition: 0.6s ease;
	border-radius: 0 3px 3px 0;
	user-select: none;
}

/* Position the "next button" to the right */
.next {
	right: 0;
	border-radius: 3px 0 0 3px;
}

/* On hover, add a black background color with a little bit see-through */
.prev:hover, .next:hover {
	background-color: rgba(0,0,0,0.8);
}

/* Caption text */
.text {
	color: #f2f2f2;
	font-size: 15px;
	padding: 8px 12px;
	position: absolute;
	bottom: 8px;
	width: 100%;
	text-align: center;
}

/* Number text (1/3 etc) */
.numbertext {
	color: #f2f2f2;
	font-size: 12px;
	padding: 8px 12px;
	position: absolute;
	top: 0;
}

/* The dots/bullets/indicators */
.dot {
	cursor: pointer;
	height: 15px;
	width: 15px;
	margin: 0 2px;
	background-color: #bbb;
	border-radius: 50%;
	display: inline-block;
	transition: background-color 0.6s ease;
}

.active, .dot:hover {
	background-color: #717171;
}

/* Fading animation */
.fade {
	animation-name: fade;
	animation-duration: 1.5s;
}

@keyframes fade {
	from {opacity: .4} 
	to {opacity: 1}
}

/* On smaller screens, decrease text size */
@media only screen and (max-width: 300px) {
	.prev, .next,.text {font-size: 11px}
}
</style>

<body>

<div class="slideshow-container">

<div class="mySlides fade">
	<img src="./assets/AI_pics/20230607PHT95601_original.jpg">
</div>

<div class="mySlides fade">
	<img src="./assets/AI_pics/ai_face2.png">
</div>

<div class="mySlides fade">
	<img src="./assets/AI_pics/real-ai.jpg">
</div>

<div class="mySlides fade">
	<img src="./assets/AI_pics/ia_face9.jpg">
</div>

<div class="mySlides fade">
	<img src="./assets/AI_pics/ai_face5.png">
</div>

</div>
<br>

<div style="text-align:center">
  <span class="dot" onclick="currentSlide(1)"></span> 
  <span class="dot" onclick="currentSlide(2)"></span> 
  <span class="dot" onclick="currentSlide(3)"></span>
  <span class="dot" onclick="currentSlide(4)"></span> 
  <span class="dot" onclick="currentSlide(5)"></span>
</div>

<script>
let slideIndex = 1; // Start from the first slide
showSlides(slideIndex); // Initialize the slideshow

function plusSlides(n) {
  slideIndex += n;
  if (slideIndex > slides.length) {slideIndex = 1}    
  if (slideIndex < 1) {slideIndex = slides.length}
  showSlides(slideIndex);
}

function currentSlide(n) {
  showSlides(slideIndex = n);
}

function showSlides(n) {
  let i;
  let slides = document.getElementsByClassName("mySlides");
  let dots = document.getElementsByClassName("dot");
  if (n > slides.length) {slideIndex = 1}    
  if (n < 1) {slideIndex = slides.length}
  for (i = 0; i < slides.length; i++) {
      slides[i].style.display = "none";  
  }
  for (i = 0; i < dots.length; i++) {
      dots[i].className = dots[i].className.replace(" active", "");
  }
  slides[slideIndex-1].style.display = "block";  
  dots[slideIndex-1].className += " active";
}
</script>

</body>
"""

custom_styles = """
<style>
	<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css">
		body {
				font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
				background-color: #f4f4f4;
		}
</style>
"""
def get_base64_encoded_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode('utf-8')

encoded_image_1 = get_base64_encoded_image("./assets/AI_pics/ai_face2.png")
encoded_image_2 = get_base64_encoded_image("./assets/AI_pics/ai_face3.png")
encoded_image_3 = get_base64_encoded_image("./assets/AI_pics/ai_face4.png")
encoded_image_4 = get_base64_encoded_image("./assets/AI_pics/ai_face5.png")
encoded_image_5 = get_base64_encoded_image("./assets/AI_pics/ai_face6.png")
encoded_image_6 = get_base64_encoded_image("./assets/AI_pics/ai_face7.png")

slideshow_html = f"""
<!DOCTYPE html>
<html>
<head>
<style>
.mySlides {{display: none;}}
.mySlides img {{
  width: 100%; /* Responsive width */
  max-width: 700px; /* Maximum width */
  height: auto; /* Adjust height automatically */
  display: block; /* Center the image */
  margin-left: auto;
  margin-right: auto;
}}
</style>
</head>
<body>

<div class="mySlides">
  <img src="data:image/png;base64,{encoded_image_1}">
</div>

<div class="mySlides">
  <img src="data:image/png;base64,{encoded_image_2}">
</div>

<div class="mySlides">
  <img src="data:image/png;base64,{encoded_image_3}">
</div>

<div class="mySlides">
  <img src="data:image/png;base64,{encoded_image_4}">
</div>

<div class="mySlides">
  <img src="data:image/png;base64,{encoded_image_5}">
</div>

<div class="mySlides">
  <img src="data:image/png;base64,{encoded_image_6}">
</div>

<script>
var slideIndex = 0;
showSlides();

function showSlides() {{
  var i;
  var slides = document.getElementsByClassName("mySlides");
  for (i = 0; i < slides.length; i++) {{
    slides[i].style.display = "none";  
  }}
  slideIndex++;
  if (slideIndex > slides.length) {{slideIndex = 1}}
  slides[slideIndex-1].style.display = "block";
  setTimeout(showSlides, 2000); // Change image every 2 seconds
}}
</script>

</body>
</html>
"""


def render_home_page():
	st.title("nextE@AI Forecasting")
	st.subheader("Forecast and analyze renewable energy production and consumption")
	stc.html(slideshow_html, height=700)
	st.markdown("""
	<style>
			.divider {
					border-bottom: 1px solid rgba(203, 228, 222, 0.2); /* Change color here */
					margin: 20px 0; /* Adjust margin to suit */
			}
	</style>
	<div class="divider"></div>
	""", unsafe_allow_html=True)
	st.markdown(custom_styles, unsafe_allow_html=True)
	st.write("Use the navigation menu to access forecasting and EDA tools.")
	st.markdown("""
	<style>
			.divider {
					border-bottom: 1px solid rgba(203, 228, 222, 0.2); /* Change color here */
					margin: 20px 0; /* Adjust margin to suit */
			}
	</style>
	<div class="divider"></div>
	""", unsafe_allow_html=True)

def main():
	
	st.sidebar.title("Navigation")

	# Initialize session state for conversation history
	if 'conversation' not in st.session_state:
		st.session_state['conversation'] = []

	# Determine the index for the default value of the sidebar radio
	# default_index = 0 if st.session_state['page'] == "Home" else ["Forecast", "EDA"].index(st.session_state['page'])

	# Use session state to set default value for sidebar radio
	page = st.sidebar.radio(
			"Select a page:",
			options=["Home", "Forecast", "Market Fundamentals", "Balancing Market"],
			index=None,
			key="page_select"
	)

	if page==None:
		render_home_page()
		st.session_state['page'] = "Home"

	# Update session state with new page selection
	st.session_state['page'] = page

	# Render the appropriate page based on session state
	if st.session_state['page'] == "Home":
		render_home_page()
	elif st.session_state["page"] == "Balancing Market":
		render_balancing_market_page()
		render_balancing_market_intraday_page()
	elif st.session_state['page'] == "Forecast":
		render_forecast_page()
	elif st.session_state['page'] == "Market Fundamentals":
		render_fundamentals_page()
	# elif st.session_state['page'] == "EDA":
	# 	render_eda_page()

if __name__ == "__main__":
		main()