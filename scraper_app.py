import sys
import os
import requests
from bs4 import BeautifulSoup, Comment
from readability import Document
from urllib.parse import urljoin, urlparse
import time
import collections
import logging # Keep logging for potential background errors
import json
import streamlit as st
from io import StringIO # To capture logs for display

# --- Basic Configuration (Defaults for the UI) ---
DEFAULT_START_URL = "https://meet.eslite.com/hk/tc/artshow"
DEFAULT_MAX_PAGES = 10
DEFAULT_DELAY = 2
DEFAULT_MIN_TEXT_LENGTH = 50
DEFAULT_OUTPUT_FILE = 'scraped_content.jsonl'
USER_AGENT = 'MyInternalLinkScraperBot/1.0 (StreamlitApp; Python Requests)'

# --- Setup Logging (Capture logs to display in Streamlit) ---
log_stream = StringIO()
# Configure logging handler to write to the StringIO buffer
log_handler = logging.StreamHandler(log_stream)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Configure root logger - remove existing handlers first to avoid duplicates if script reruns
root_logger = logging.getLogger()
if root_logger.hasHandlers():
    root_logger.handlers.clear()
root_logger.addHandler(log_handler)
root_logger.setLevel(logging.INFO)


# --- Helper Functions (Unchanged) ---

def fetch_html(url, session):
    """Fetches HTML content from a URL using a requests session."""
    headers = {'User-Agent': USER_AGENT}
    try:
        response = session.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        content_type = response.headers.get('content-type', '').lower()
        if 'html' not in content_type:
            logging.warning(f"Skipped non-HTML content at {url} (Type: {content_type})")
            return None
        return response.text
    except requests.exceptions.Timeout:
        logging.error(f"Timeout error fetching {url}")
        return None
    except requests.exceptions.TooManyRedirects:
        logging.error(f"Too many redirects for {url}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching {url}: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred during fetch for {url}: {e}")
        return None

def clean_html_text(html_content):
    """Extracts main textual content from HTML using readability-lxml."""
    if not html_content:
        return None
    try:
        doc = Document(html_content)
        cleaned_html = doc.summary()
        soup = BeautifulSoup(cleaned_html, 'lxml')
        for script_or_style in soup(["script", "style"]):
            script_or_style.decompose()
        for element in soup(text=lambda text: isinstance(text, Comment)):
            element.extract()
        text = soup.get_text(separator=' ', strip=True)
        text = ' '.join(text.split())
        return text
    except Exception as e:
        logging.error(f"Error cleaning HTML with readability/BeautifulSoup: {e}")
        return None

def find_internal_links(html_content, base_url):
    """Finds all unique internal links on a page."""
    links = set()
    if not html_content:
        return list(links)
    soup = BeautifulSoup(html_content, 'lxml')
    try:
        base_domain = urlparse(base_url).netloc
        if not base_domain: # Handle cases where base_url might be invalid
             logging.warning(f"Could not determine base domain for URL: {base_url}")
             return list(links)
    except ValueError:
        logging.warning(f"Could not parse base URL: {base_url}")
        return list(links)


    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href'].strip()
        if href and not href.startswith(('#', 'mailto:', 'tel:', 'javascript:')):
            try:
                absolute_url = urljoin(base_url, href)
                parsed_url = urlparse(absolute_url)
                if parsed_url.scheme in ['http', 'https'] and parsed_url.netloc == base_domain:
                    clean_url = parsed_url._replace(fragment="").geturl()
                    links.add(clean_url)
            except ValueError:
                pass # Ignore invalid URLs silently
    return list(links)

# --- Modified Crawling Logic for Streamlit ---
# Takes a placeholder for the FINAL log display
def crawl_website_streamlit(start_url, max_pages, politeness_delay, min_text_length, output_file,
                            status_placeholder, progress_placeholder, log_display_placeholder):
    """Crawls website and updates Streamlit UI elements. Logs are displayed only at the end."""
    session = requests.Session()
    queue = collections.deque([start_url])
    visited_urls = {start_url}
    pages_scraped_count = 0
    data_saved_count = 0
    all_results = [] # Store results in memory for final file generation
    crawl_error_occurred = False # Flag to track if errors happened

    # Clear previous logs from the StringIO buffer *and* the placeholder
    log_stream.seek(0)
    log_stream.truncate(0)
    log_display_placeholder.empty() # Clear the placeholder content

    status_placeholder.info(f"Starting crawl from: {start_url}")
    progress_bar = progress_placeholder.progress(0)

    # Clear the output file at the start of a new crawl
    try:
        with open(output_file, 'w', encoding='utf-8') as f_out:
            pass # Just open in 'w' to clear it
    except IOError as e:
        status_placeholder.error(f"Error clearing output file {output_file}: {e}")
        logging.error(f"Error clearing output file {output_file}: {e}") # Log the error
        crawl_error_occurred = True
        # Display logs immediately if we can't even start
        log_stream.seek(0)
        log_display_placeholder.text_area("Logs", log_stream.read(), height=250, key="log_display_area_final")
        return None # Stop crawl

    # Main crawling loop
    try:
        while queue and pages_scraped_count < max_pages:
            current_url = queue.popleft()
            pages_scraped_count += 1
            progress_value = min(1.0, pages_scraped_count / max_pages)
            progress_bar.progress(progress_value)
            status_placeholder.info(f"[{pages_scraped_count}/{max_pages}] Scraping: {current_url}")
            logging.info(f"Requesting: {current_url}") # Log actions

            html = fetch_html(current_url, session)

            if html:
                logging.info(f"Processing content from: {current_url}")
                cleaned_text = clean_html_text(html)
                if cleaned_text and len(cleaned_text) >= min_text_length:
                    data = {'url': current_url, 'text': cleaned_text}
                    all_results.append(data)
                    data_saved_count += 1
                    logging.info(f"Saved text from {current_url} (Length: {len(cleaned_text)})")

                    internal_links = find_internal_links(html, current_url)
                    new_links_found = 0
                    for link in internal_links:
                        if link not in visited_urls:
                            visited_urls.add(link)
                            queue.append(link)
                            new_links_found += 1
                    if new_links_found > 0:
                         logging.info(f"Added {new_links_found} new links to queue.")

                elif cleaned_text:
                    logging.warning(f"Text from {current_url} too short ({len(cleaned_text)} chars), skipping.")
                else:
                    logging.warning(f"Could not extract clean text from {current_url}")
            else:
                 logging.warning(f"Failed to fetch HTML from {current_url}, skipping.")

            # --- NO log display update inside the loop ---

            if queue and pages_scraped_count < max_pages:
                time.sleep(politeness_delay)

    except Exception as e:
        status_placeholder.error(f"An unexpected error occurred during the crawl: {e}")
        logging.error("Crawling error:", exc_info=True) # Log full traceback
        crawl_error_occurred = True
        # --- NO immediate log display update here ---
        # Error will be logged to the stream and shown at the end
    finally:
        session.close()
        progress_bar.progress(1.0) # Ensure progress bar completes

        # --- Display final logs HERE ---
        log_stream.seek(0)
        final_log_text = log_stream.read()
        # Use the placeholder to display the text_area ONCE at the end
        # Use a unique key just in case, though it might not be strictly needed now.
        log_display_placeholder.text_area("Logs", final_log_text, height=250, key="log_display_area_final")

    # Write all collected results to the file at the end (if no critical error occurred before write)
    write_error = False
    if not crawl_error_occurred: # Only attempt write if crawl didn't fail critically earlier
        try:
            with open(output_file, 'w', encoding='utf-8') as f_out:
                for item in all_results:
                    f_out.write(json.dumps(item, ensure_ascii=False) + '\n')
        except IOError as e:
            status_placeholder.error(f"Error writing final results to {output_file}: {e}")
            logging.error(f"Error writing final results to {output_file}: {e}")
            write_error = True
            # Re-display logs if write fails, as it's new info
            log_stream.seek(0)
            log_display_placeholder.text_area("Logs", log_stream.read(), height=250, key="log_display_area_final_write_error")


    # Final status update
    if not crawl_error_occurred and not write_error:
        finish_message = ""
        if not queue and pages_scraped_count < max_pages:
            finish_message = f"Crawl finished: No more unique links found. Scraped {pages_scraped_count} pages."
        elif pages_scraped_count >= max_pages:
            finish_message = f"Crawl finished: Reached max page limit ({max_pages})."
        else:
             finish_message = f"Crawl finished. Scraped {pages_scraped_count} pages."

        status_placeholder.success(f"{finish_message}\nEncountered {len(visited_urls)} unique URLs. Saved text from {data_saved_count} pages to {output_file}.")
        logging.info(finish_message)
        logging.info(f"Total unique URLs encountered: {len(visited_urls)}")
        logging.info(f"Total pages saved: {data_saved_count}")
    elif write_error:
         # Status already updated about write error
         pass
    else:
        # Status already updated about crawl error
        pass


    # Return path to the results file for download button (even if write failed, button might show error)
    if not crawl_error_occurred and not write_error:
        return output_file
    else:
        return None # Indicate failure


# --- Streamlit App Layout ---
st.set_page_config(layout="wide")
st.title("Web Scraper For My Dearest Friend Mr Yung")

st.sidebar.header("Configuration")
start_url = st.sidebar.text_input("Start URL", DEFAULT_START_URL)
max_pages = st.sidebar.number_input("Max Pages to Scrape", min_value=1, max_value=1000, value=DEFAULT_MAX_PAGES, step=1)
politeness_delay = st.sidebar.number_input("Delay Between Requests (seconds)", min_value=0.0, max_value=10.0, value=float(DEFAULT_DELAY), step=0.5)
min_text_length = st.sidebar.number_input("Min Text Length to Save", min_value=0, value=DEFAULT_MIN_TEXT_LENGTH, step=10)
output_file = st.sidebar.text_input("Output Filename (.jsonl)", DEFAULT_OUTPUT_FILE)

# Placeholders for dynamic content
status_placeholder = st.empty()
progress_placeholder = st.empty()
download_placeholder = st.empty()

# Expander for logs
log_expander = st.expander("Show Logs", expanded=False)
# Placeholder *inside* the expander specifically for the FINAL log display
log_display_final_placeholder = log_expander.empty()


if st.sidebar.button("Start Crawling"):
    # --- Validate Start URL ---
    is_valid_url = False
    try:
        parsed_start_url = urlparse(start_url)
        if all([parsed_start_url.scheme, parsed_start_url.netloc]):
             is_valid_url = True
        else:
            status_placeholder.error(f"Error: Start URL '{start_url}' seems invalid. Please use a full URL (e.g., https://example.com).")
    except ValueError:
         status_placeholder.error(f"Error: Could not parse Start URL '{start_url}'.")

    if is_valid_url:
        # Clear previous results/download button
        download_placeholder.empty()
        status_placeholder.info("Starting crawl process...")
        # Ensure the log display placeholder is cleared before starting
        log_display_final_placeholder.empty()
        # Make expander visible/expanded when crawl starts
        log_expander.expanded = True # Programmatically expand

        # Run the crawl, passing the placeholder inside the expander for FINAL display
        result_file_path = crawl_website_streamlit(
            start_url,
            max_pages,
            politeness_delay,
            min_text_length,
            output_file,
            status_placeholder,
            progress_placeholder,
            log_display_final_placeholder # Pass the placeholder for final log display
        )

        # If crawl function indicated success (returned a path), provide download button
        if result_file_path: # Check if path was returned (indicates no critical errors before/during write)
             if os.path.exists(result_file_path):
                 try:
                     with open(result_file_path, 'r', encoding='utf-8') as f:
                         file_content = f.read()
                     # Place the download button using its placeholder
                     download_placeholder.download_button(
                         label=f"Download {output_file}",
                         data=file_content,
                         file_name=output_file,
                         mime='application/jsonl'
                     )
                 except Exception as e:
                     status_placeholder.error(f"Error reading result file for download: {e}")
             else:
                  # This case means write should have happened but file isn't there - internal logic error or permissions?
                  status_placeholder.warning(f"Crawling process finished, but result file {result_file_path} was not found.")
        # else: crawl function returned None, indicating an error occurred and was handled/logged.

else:
    st.info("Configure settings in the sidebar and click 'Start Crawling'.")