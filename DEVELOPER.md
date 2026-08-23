# Yue - Developer Guide: Adding a New TTS Model

Yue is designed to automatically discover and use any TTS model that adheres to a specific "contract." To add a new model, you only need to create one Python file and edit the configuration.

The process is:
1.  Create a new file in `yue/tts/` named `yourtts_tts.py`. The filename has to end with `_tts.py`.
2.  Implement a class inside that file that inherits from `TTSBase`.
3.  Add the model's dependencies to `requirements.txt`.
4.  Add a default voice for the model in `config.py`'s `TTS_VOICES` dictionary.
5.  If the model requires a language code, add a default to `config.py`'s `TTS_LANGUAGE_CODES` dictionary.

### The `TTSBase` Contract

Your new class must implement the following properties and methods. The `Yue` application relies on this exact structure to function correctly.

-   `__init__(self, console: Console, voice: str = None, lang: str = None):`
    -   **Purpose:** The constructor for your TTS model.
    -   **Rules:** It must accept `console`, `voice`, and `lang` and pass them to the base class constructor: `super().__init__(console, voice, lang)`. The `voice` and `lang` values are passed from the command-line arguments (`--voice` and `--lang`). Not all TTS models support language selection; if yours doesn't, you can simply ignore the `lang` parameter.

-   `@property name(self) -> str:`
    -   **Purpose:** A unique, lowercase identifier for the model.
    -   **Rules:** Must match the filename (e.g., if the file is `yourtts_tts.py`, this must return `"yourtts"`). This is used for command-line arguments and configuration keys.

-   `@property output_format(self) -> str:`
    -   **Purpose:** The audio format the model produces.
    -   **Rules:** Must be `"mp3"` or `"wav"`. This tells the audio pipeline how to process the output files.

-   `async def initialize(self) -> bool:`
    -   **Purpose:** Prepare the model. This is where you should handle imports, check for API keys, and load models.
    -   **Rules:**
        -   It **must** be asynchronous.
        -   It **must** gracefully handle a missing dependency by wrapping imports in a `try...except ImportError` block and returning `False`.
        -   It **must** return `True` on success and `False` on failure.
        -   For long-running tasks (like downloading models), use `self.console.print()` to give the user feedback.
        -   If model loading is a blocking operation, it **must** be run in a separate thread to avoid freezing the UI (see template).

-   `async def generate_audio(self, text: str, output_path: str):`
    -   **Purpose:** The core function that converts a string of text into an audio file.
    -   **Rules:**
        -   It **must** be asynchronous.
        -   It **must** save the generated audio to the exact `output_path` provided. The audio pipeline depends on this file existing after the method completes.
        -   Like `initialize`, any blocking TTS generation code **must** be run in a separate thread.

### Word-Level Timing (Optional Advanced Feature)

Yue supports word-level highlighting during audio playback, which provides a better reading experience by highlighting each word as it's spoken. The timing logic has been centralized in `yue/timing_calculator.py` to simplify TTS implementations.

To support word-level highlighting, your TTS model should override the `get_raw_timing_data` method from the `TTSBase` class:

-   `async def get_raw_timing_data(self, text: str, output_path: str):`
    -   **Purpose:** Extract raw word timing data from your TTS engine and generate the audio file.
    -   **Return Value:** A list of `(word, start_time, end_time)` tuples with raw timing data from your TTS engine
    -   **Implementation Guidelines:**
        - Generate the audio file and save it to `output_path`
        - Extract timing data directly from your TTS engine's output
        - Return the raw timing data without any processing or adjustments
        - The centralized timing calculator will handle all processing, continuity adjustments, and word mapping
        - If your TTS engine doesn't provide timing data, return an empty list `[]`

The base class `generate_audio_with_timing()` method automatically:
- Calls your `get_raw_timing_data()` method
- Processes the raw timing data using the centralized timing calculator
- Returns a structured dictionary with processed timing information
- Handles fallback estimation if no raw timing data is available

Example implementation pattern:

```python
async def get_raw_timing_data(self, text: str, output_path: str):
    """
    Extract raw word timing data from the TTS engine.
    """
    # Generate audio and extract raw timing from the TTS engine
    result = self.tts_engine.synthesize_with_timing(text, voice=self.voice)
    
    # Save audio to file
    with open(output_path, 'wb') as f:
        f.write(result.audio_data)
    
    # Extract raw word timings from engine results (no processing needed)
    word_timings = []
    for word_info in result.word_timings:
        word_timings.append((
            word_info.text,      # The actual word text
            word_info.start,     # Start time in seconds (raw from engine)
            word_info.end        # End time in seconds (raw from engine)
        ))
    
    # Return raw timing data - the timing calculator will handle all processing
    return word_timings
```

**Note:** You don't need to implement `generate_audio_with_timing()` unless you have special requirements. The base class implementation automatically uses your `get_raw_timing_data()` method and processes the results through the centralized timing calculator.

### Code Template

Use this template for your `yue/tts/yourtts_tts.py` file. It includes the required structure and best practices for handling errors and blocking operations.

```python
# yue/tts/yourtts_tts.py
import os
import asyncio
import logging
from rich.console import Console

# TODO: Import any other libraries your TTS model needs.

from .base import TTSBase
from .. import config

class YourTTSTTS(TTSBase):
    """
    A brief description of your TTS model and what it does.
    """
    
    @property
    def name(self) -> str:
        # Must match the filename: yourtts_tts.py -> "yourtts"
        return "yourtts"

    @property
    def output_format(self) -> str:
        # The audio format your model produces ("mp3" or "wav")
        return "mp3"

    def __init__(self, console: Console, voice: str = None, lang: str = None):
        super().__init__(console, voice, lang)
        self.client = None # Example: an API client or model object
        
        # If the user doesn't provide a voice via --voice, use the default from config.py
        if self.voice is None:
            self.voice = config.TTS_VOICES.get(self.name)
        
        # If the user doesn't provide a language via --lang, use the default from config.py
        # Only use self.lang if your TTS model actually supports it.
        if self.lang is None:
            self.lang = config.TTS_LANGUAGE_CODES.get(self.name)

    async def initialize(self) -> bool:
        """
        Prepare the model. Check dependencies, load models, etc.
        """
        # 1. Check for dependencies and handle failure gracefully.
        try:
            # TODO: Import the actual TTS library here.
            # from your_tts_library import YourTTSClient
            pass # Replace with actual import
        except ImportError:
            self.console.print("[bold red]Error: 'your_tts_library' package not found.[/bold red]")
            self.console.print("[yellow]Please install it with 'pip install your_tts_library'[/yellow]")
            logging.error("'your_tts_library' is not installed.")
            return False

        # 2. For heavy/blocking setup tasks (like downloading or loading a large model),
        #    run them in a separate thread to keep the UI responsive.
        loop = asyncio.get_running_loop()
        try:
            self.console.print("[cyan]Initializing YourTTS model... (this may take a moment)[/cyan]")
            
            # This is a synchronous function to do the heavy lifting.
            def _blocking_init():
                # TODO: Replace with your actual model loading logic.
                # For example, check for API keys or load a model using self.lang
                # client = YourTTSClient(api_key=os.environ.get("YOURTTS_API_KEY"), language=self.lang)
                # return client
                return True # Return the client/model object on success

            # Run the blocking function in an executor.
            self.client = await loop.run_in_executor(None, _blocking_init)
            
            if not self.client:
                self.console.print("[bold red]YourTTS initialization failed. Check logs for details.[/bold red]")
                return False

            self.initialized = True
            self.console.print("[green]YourTTS model initialized successfully.[/green]")
            return True
        except Exception as e:
            self.console.print(f"[bold red]An unexpected error occurred during YourTTS initialization: {e}[/bold red]")
            logging.error("YourTTS async initialization failed.", exc_info=True)
            return False

    async def generate_audio(self, text: str, output_path: str):
        """
        Generate audio from text and save it to the given path.
        """
        if not self.initialized or not self.client:
            raise RuntimeError("YourTTS has not been initialized.")

        # This is a synchronous function to do the audio generation.
        def _blocking_generate():
            try:
                # TODO: Replace with your library's actual audio generation call.
                # Use self.voice, which was set in __init__
                # The final audio MUST be saved to the `output_path`.
                # audio_data = self.client.generate(text=text, voice=self.voice)
                # with open(output_path, "wb") as f:
                #     f.write(audio_data)
                pass # Replace with actual generation
            except Exception as e:
                logging.error(f"Error during YourTTS audio generation: {e}", exc_info=True)
                raise e
        
        # Run the blocking generation in a separate thread.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _blocking_generate)

    # Optional: Implement word-level timing for better highlighting accuracy
    async def get_raw_timing_data(self, text: str, output_path: str):
        """
        Extract raw word timing data from your TTS engine.
        
        This method is optional but recommended for better word highlighting accuracy.
        If not implemented, the base class will use fallback estimation.
        
        Returns:
            List of (word, start_time, end_time) tuples with raw timing data from TTS engine,
            or empty list if no timing data is available
        """
        # TODO: Implement actual timing extraction from your TTS engine
        # The example below shows the structure but should be replaced with
        # actual implementation based on your TTS engine's capabilities.
        
        # This is a synchronous function to do the audio generation with timing.
        def _blocking_generate():
            try:
                # TODO: Replace with your library's actual audio generation call that provides timing.
                # Use self.voice, which was set in __init__
                # The final audio MUST be saved to the `output_path`.
                
                # Example with a hypothetical TTS engine that provides timing:
                # result = self.client.generate_with_timing(text=text, voice=self.voice)
                # with open(output_path, "wb") as f:
                #     f.write(result.audio_data)
                
                # Extract raw timing data from your TTS engine
                # word_timings = []
                # for word_info in result.word_timings:
                #     word_timings.append((
                #         word_info.text,      # The actual word text
                #         word_info.start,     # Start time in seconds (raw from engine)
                #         word_info.end        # End time in seconds (raw from engine)
                #     ))
                # return word_timings
                
                # If your TTS engine doesn't provide timing, fall back to regular generation
                # and return empty list (the timing calculator will estimate)
                # TODO: Replace with actual generation
                pass # Replace with actual generation
                return []  # Return empty list if no timing available
                
            except Exception as e:
                logging.error(f"Error during YourTTS audio generation: {e}", exc_info=True)
                raise e
        
        # Run the blocking generation in a separate thread.
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _blocking_generate)

```