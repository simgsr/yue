"""Abstract base class for TTS models in the Yue eBook reader."""

from abc import ABC, abstractmethod
from rich.console import Console


class TTSBase(ABC):
    """
    Abstract base class for all TTS models.
    
    This class defines the interface that all TTS models must implement
    to be compatible with the Yue eBook reader.
    """

    def __init__(self, console: Console, voice: str = None, lang: str = None):
        """
        Initialize the TTS model.
        
        Args:
            console: Rich console instance for user feedback
            voice: Optional voice for the TTS model
            lang: Optional language for the TTS model
        """
        self.console = console
        self.voice = voice
        self.lang = lang
        self.initialized = False

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Get the unique identifier for this TTS model.
        
        Returns:
            str: Model name (e.g., 'edge', 'kokoro')
        """
        pass

    @property
    @abstractmethod
    def output_format(self) -> str:
        """
        Get the audio format this model produces.
        
        Returns:
            str: File extension without dot (e.g., 'mp3', 'wav')
        """
        pass

    @abstractmethod
    async def initialize(self) -> bool:
        """
        Initialize the TTS model asynchronously.
        
        This method should:
        - Check for required dependencies
        - Load models if necessary
        - Handle ImportErrors gracefully
        - Set self.initialized = True on success
        
        Returns:
            bool: True if initialization succeeded, False otherwise
        """
        pass

    @abstractmethod
    async def generate_audio(self, text: str, output_path: str):
        """
        Generate audio from text and save to file.
        
        Args:
            text: Text to convert to speech
            output_path: Full path where audio file should be saved
            
        Raises:
            RuntimeError: If model is not initialized
            Exception: If audio generation fails
        """
        pass

    async def generate_audio_with_timing(self, text: str, output_path: str):
        """
        Generate audio from text and save to file, returning processed timing information.
        
        This method now uses the centralized timing calculator to process timing data.
        TTS implementations should override get_raw_timing_data() to provide engine-specific
        timing information, while this method handles all the processing and adjustments.
        
        Args:
            text: Text to convert to speech
            output_path: Full path where audio file should be saved
            
        Returns:
            dict: Processed timing information containing:
                - word_timings: List of (word, start_time, end_time) tuples
                - speech_duration: Duration of speech content
                - total_duration: Total audio duration
                - word_mapping: Mapping from original words to TTS timings
            
        Raises:
            RuntimeError: If model is not initialized
            Exception: If audio generation fails
        """
        # Generate audio first
        await self.generate_audio(text, output_path)

        # Get raw timing data from the TTS implementation
        raw_timings = await self.get_raw_timing_data(text, output_path)
        return await self._finalize_timing_data(text, raw_timings, output_path)

    async def _finalize_timing_data(self, text: str, raw_timings, output_path: str):
        """Measure the generated audio and process raw timings into the
        word-mapped format the reader's highlight sync expects."""
        from .. import audio
        from ..timing_calculator import process_tts_timing_data
        duration = await audio.get_audio_duration(output_path)
        return process_tts_timing_data(text, raw_timings, duration)

    async def get_raw_timing_data(self, text: str, output_path: str):
        """
        Get raw timing data from the TTS engine.
        
        This method should be overridden by TTS implementations that can provide
        precise timing information. The default implementation returns empty list,
        which will cause the timing calculator to estimate timings.
        
        Args:
            text: Text that was converted to speech
            output_path: Path to the generated audio file
            
        Returns:
            List of (word, start_time, end_time) tuples with raw timing data from TTS engine,
            or empty list if no timing data is available
        """
        return []

    async def warm_up(self):
        """
        Warm up the model to reduce initial latency.
        
        This is called once after initialization to prepare the model
        for faster subsequent audio generation.
        """
        pass

    async def shutdown(self):
        """
        Release resources held by this model (e.g. terminate a worker
        subprocess). Called when the model is replaced or the app exits.
        The default implementation is a no-op; subprocess backends override it.
        """
        pass

    def get_overlap_seconds(self) -> float | None:
        """
        Get the TTS-specific overlap seconds for this model.
        
        Returns:
            float: Overlap seconds specific to this TTS model, or None to use default
        """
        from .. import config
        return config.TTS_OVERLAP_SECONDS.get(self.name)

    def get_paragraph_pause_seconds(self) -> float:
        """
        Get the extra pause (seconds) to insert at paragraph boundaries for
        this model. Falls back to the global PARAGRAPH_PAUSE_SECONDS.
        """
        from .. import config
        return config.TTS_PARAGRAPH_PAUSE_SECONDS.get(
            self.name, config.PARAGRAPH_PAUSE_SECONDS
        )