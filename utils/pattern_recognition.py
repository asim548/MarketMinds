"""
Pattern Recognition Utility
Handles candlestick pattern detection using YOLO model
"""
import os
import shutil
from werkzeug.utils import secure_filename

# Writable parent only — Ultralytics adds its own subfolder under YOLO_CONFIG_DIR.
if not (os.environ.get("YOLO_CONFIG_DIR") or "").strip():
    os.environ["YOLO_CONFIG_DIR"] = os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp"

# Try to import torch gracefully
try:
    import torch
    TORCH_AVAILABLE = True
    _device = 'cuda' if torch.cuda.is_available() else 'cpu'
except ImportError:
    TORCH_AVAILABLE = False
    _device = 'cpu'
    print("Warning: torch not available. Pattern recognition will be disabled.")

# Try to import YOLO, handle gracefully if not available
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True and TORCH_AVAILABLE
except ImportError:
    YOLO_AVAILABLE = False
    print("Warning: ultralytics not available. Pattern recognition will be disabled.")

class PatternRecognizer:
    def __init__(self, model_path='best.pt', upload_folder='static/uploads/patterns', results_folder='static/results/patterns'):
        self.model = None
        self.model_path = model_path
        self.upload_folder = upload_folder
        self.results_folder = results_folder
        self.device = _device

        # Create necessary folders
        os.makedirs(self.upload_folder, exist_ok=True)
        os.makedirs(self.results_folder, exist_ok=True)

        # Load model
        self._load_model()

    def _load_model(self):
        """Load the YOLO model"""
        if not YOLO_AVAILABLE:
            print("YOLO/torch not available. Pattern recognition disabled.")
            return

        try:
            if os.path.exists(self.model_path):
                self.model = YOLO(self.model_path)
                print(f"Pattern Recognition Model loaded successfully on device: {self.device}")
            else:
                print(f"Warning: Model file '{self.model_path}' not found. Pattern recognition will not work.")
        except Exception as e:
            print(f"Error loading YOLO model: {e}")
            self.model = None

    def is_ready(self):
        return self.model is not None and YOLO_AVAILABLE

    def allowed_file(self, filename):
        ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

    def predict(self, file_bytes, original_filename):
        if not self.is_ready():
            return {
                'success': False,
                'patterns': [],
                'result_image_url': None,
                'original_image_url': None,
                'error': 'Pattern recognition model not available (torch/ultralytics not installed or best.pt missing).'
            }

        try:
            filename = secure_filename(original_filename)
            input_path = os.path.join(self.upload_folder, filename)

            with open(input_path, 'wb') as f:
                f.write(file_bytes)

            results = self.model.predict(
                source=input_path,
                save=True,
                project='runs/detect',
                name='predict',
                exist_ok=True,
                conf=0.25,
                verbose=False
            )

            unique_patterns = set()
            for r in results:
                names = r.names
                for c in r.boxes.cls:
                    unique_patterns.add(names[int(c)])

            detected_patterns = sorted(list(unique_patterns))

            yolo_output_dir = 'runs/detect/predict'
            possible_output = os.path.join(yolo_output_dir, filename)
            result_filename = f"result_{filename}"
            result_path = os.path.join(self.results_folder, result_filename)
            result_image_url = f"/static/uploads/patterns/{filename}"

            if os.path.exists(possible_output):
                shutil.copy(possible_output, result_path)
                result_image_url = f"/static/results/patterns/{result_filename}"
            elif os.path.exists(yolo_output_dir):
                for file_in_dir in os.listdir(yolo_output_dir):
                    if file_in_dir.lower().endswith(('.png', '.jpg', '.jpeg')):
                        shutil.copy(os.path.join(yolo_output_dir, file_in_dir), result_path)
                        result_image_url = f"/static/results/patterns/{result_filename}"
                        break

            return {
                'success': True,
                'patterns': detected_patterns,
                'result_image_url': result_image_url,
                'original_image_url': f"/static/uploads/patterns/{filename}",
                'original_filename': filename
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'patterns': [],
                'result_image_url': None,
                'original_image_url': None,
                'error': f'Error during prediction: {str(e)}'
            }