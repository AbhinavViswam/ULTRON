import cv2
import mediapipe as mp
import threading
import time
import pyautogui
pyautogui.FAILSAFE = False

import os
import urllib.request

class GestureController:
    """Background gesture controller using OpenCV and MediaPipe Tasks API."""

    def __init__(self, core):
        self.core = core
        self.active = False
        self._thread = None
        self.cap = None
        
        self.hand_model_path = os.path.join(os.path.dirname(__file__), "hand_landmarker.task")
        self.face_model_path = os.path.join(os.path.dirname(__file__), "blaze_face_short_range.tflite")
        
        self._ensure_models()

    def _ensure_models(self):
        hand_url = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
        face_url = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"
        
        if not os.path.exists(self.hand_model_path):
            print(f"[Gesture] Downloading hand landmarker model to {self.hand_model_path}...")
            urllib.request.urlretrieve(hand_url, self.hand_model_path)
            print("[Gesture] Hand landmarker downloaded.")
            
        if not os.path.exists(self.face_model_path):
            print(f"[Gesture] Downloading face detector model to {self.face_model_path}...")
            urllib.request.urlretrieve(face_url, self.face_model_path)
            print("[Gesture] Face detector downloaded.")

    def start(self):
        if self.active:
            return
        self.active = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.active = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self.cap:
            self.cap.release()
            self.cap = None

    def _loop(self):
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        
        # Open camera only when loop starts
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            print("[Gesture] Error: Could not open camera.")
            self.active = False
            return
            
        print("[Gesture] Camera activated.")

        screen_w, screen_h = pyautogui.size()
        
        # State tracking for pinch click
        was_left_pinching = False
        was_right_pinching = False
        last_presence = time.time()
        
        # Smoothing for mouse movement
        smooth_x, smooth_y = 0, 0
        smoothing_factor = 0.25 # Lower is smoother but laggier
        
        # Relative movement tracking (like a trackpad)
        last_move_x = None
        last_move_y = None
        mouse_sensitivity = 1.2 # Adjust this to change how far the cursor moves
        # Pinch debounce and click vs drag tracking
        left_pinch_release_counter = 0
        right_pinch_release_counter = 0
        left_pinch_start_time = 0
        right_pinch_start_time = 0
        left_drag_started = False
        right_drag_started = False
        left_last_click_time = 0
        right_last_click_time = 0
        
        # Scroll tracking
        last_scroll_y = None
        
        # Initialize Hand Landmarker
        hand_base_options = python.BaseOptions(model_asset_path=self.hand_model_path)
        hand_options = vision.HandLandmarkerOptions(
            base_options=hand_base_options, 
            num_hands=1,
            min_hand_detection_confidence=0.7,
            min_hand_presence_confidence=0.7,
            min_tracking_confidence=0.7
        )
        hand_detector = vision.HandLandmarker.create_from_options(hand_options)
        
        # Initialize Face Detector
        face_base_options = python.BaseOptions(model_asset_path=self.face_model_path)
        face_options = vision.FaceDetectorOptions(
            base_options=face_base_options,
            min_detection_confidence=0.5
        )
        face_detector = vision.FaceDetector.create_from_options(face_options)
        
        frame_count = 0
        try:
            while self.active:
                ret, frame = self.cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue

                # Flip horizontally for selfie-view, then convert color space
                frame = cv2.flip(frame, 1)
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
                # Convert to MediaPipe Image
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

                # --- 1. Face Presence Detection (Throttled to save CPU) ---
                frame_count += 1
                if frame_count % 15 == 0:
                    face_results = face_detector.detect(mp_image)
                    if face_results.detections:
                        time_away = time.time() - last_presence
                        if time_away > 300: # 5 minutes away
                            from ultron.config import config
                            if config.get("auto_welcome", False):
                                self.core.say("Welcome back, sir.")
                        last_presence = time.time()

                # --- 2. Virtual Mouse (Hand Tracking) ---
                hand_results = hand_detector.detect(mp_image)
                
                if hand_results.hand_landmarks:
                    for hand_landmarks in hand_results.hand_landmarks:
                        # Get all relevant landmarks
                        index_tip = hand_landmarks[8]
                        middle_tip = hand_landmarks[12]
                        thumb_tip = hand_landmarks[4]
                        
                        # Finger up states (Tip Y < PIP Y)
                        is_index_up = hand_landmarks[8].y < hand_landmarks[6].y
                        is_middle_up = hand_landmarks[12].y < hand_landmarks[10].y
                        is_ring_up = hand_landmarks[16].y < hand_landmarks[14].y
                        is_pinky_up = hand_landmarks[20].y < hand_landmarks[18].y
                        
                        # Pinches (independent of move state)
                        dist_left = ((index_tip.x - thumb_tip.x)**2 + (index_tip.y - thumb_tip.y)**2)**0.5
                        dist_right = ((middle_tip.x - thumb_tip.x)**2 + (middle_tip.y - thumb_tip.y)**2)**0.5
                        
                        is_left_pinching = dist_left < 0.05
                        is_right_pinching = dist_right < 0.05
                        
                        if is_left_pinching:
                            left_pinch_release_counter = 0
                            if not was_left_pinching:
                                left_pinch_start_time = time.time()
                                was_left_pinching = True
                                left_drag_started = False
                            else:
                                # If held for > 0.4 seconds, initiate a drag
                                if time.time() - left_pinch_start_time > 0.4 and not left_drag_started:
                                    pyautogui.mouseDown(button='left', _pause=False)
                                    left_drag_started = True
                        else:
                            if was_left_pinching:
                                left_pinch_release_counter += 1
                                if left_pinch_release_counter >= 3:
                                    if left_drag_started:
                                        pyautogui.mouseUp(button='left', _pause=False)
                                    else:
                                        if time.time() - left_last_click_time < 0.4:
                                            pyautogui.doubleClick(button='left', _pause=False)
                                            left_last_click_time = 0
                                        else:
                                            pyautogui.click(button='left', _pause=False)
                                            left_last_click_time = time.time()
                                    was_left_pinching = False
                                    left_drag_started = False
                                    
                        if is_right_pinching:
                            right_pinch_release_counter = 0
                            if not was_right_pinching:
                                right_pinch_start_time = time.time()
                                was_right_pinching = True
                                right_drag_started = False
                            else:
                                if time.time() - right_pinch_start_time > 0.4 and not right_drag_started:
                                    pyautogui.mouseDown(button='right', _pause=False)
                                    right_drag_started = True
                        else:
                            if was_right_pinching:
                                right_pinch_release_counter += 1
                                if right_pinch_release_counter >= 3:
                                    if right_drag_started:
                                        pyautogui.mouseUp(button='right', _pause=False)
                                    else:
                                        if time.time() - right_last_click_time < 0.4:
                                            pyautogui.doubleClick(button='right', _pause=False)
                                            right_last_click_time = 0
                                        else:
                                            pyautogui.click(button='right', _pause=False)
                                            right_last_click_time = time.time()
                                    was_right_pinching = False
                                    right_drag_started = False
                        
                        # 1. Scroll (3 Fingers: Index, Middle, Ring up AND not pinching)
                        if is_index_up and is_middle_up and is_ring_up and not is_pinky_up and not is_left_pinching and not is_right_pinching:
                            if last_scroll_y is not None:
                                delta_y = index_tip.y - last_scroll_y
                                scroll_amount = int(-delta_y * screen_h * 1.5) # Sensitivity multiplier
                                if abs(scroll_amount) > 5: # Deadzone to prevent micro jitters
                                    pyautogui.scroll(scroll_amount, _pause=False)
                                    last_scroll_y = index_tip.y
                            else:
                                last_scroll_y = index_tip.y
                            
                        # 2. Move (2 Fingers: Index, Middle up OR Dragging)
                        elif ((is_index_up and is_middle_up and not is_ring_up and not is_pinky_up) and not is_left_pinching and not is_right_pinching) or left_drag_started or right_drag_started:
                            last_scroll_y = None # Reset scroll anchor
                            
                            # Smooth the raw camera coordinates first
                            smoothing_factor = 0.25 # Original smooth factor
                            if smooth_x == 0 and smooth_y == 0:
                                smooth_x = index_tip.x
                                smooth_y = index_tip.y
                            else:
                                smooth_x += (index_tip.x - smooth_x) * smoothing_factor
                                smooth_y += (index_tip.y - smooth_y) * smoothing_factor
                                
                            if last_move_x is not None and last_move_y is not None:
                                # Calculate delta (how much the finger moved since last frame)
                                dx = smooth_x - last_move_x
                                dy = smooth_y - last_move_y
                                
                                # Convert delta to screen pixels
                                move_x = dx * screen_w * mouse_sensitivity
                                move_y = dy * screen_h * mouse_sensitivity
                                
                                # Move relative to current mouse position (like a trackpad)
                                pyautogui.move(int(move_x), int(move_y), _pause=False)
                            
                            last_move_x = smooth_x
                            last_move_y = smooth_y
                        else:
                            last_scroll_y = None # Reset scroll anchor
                            last_move_x = None
                            last_move_y = None
                else:
                    if was_left_pinching:
                        if left_drag_started:
                            pyautogui.mouseUp(button='left', _pause=False)
                        else:
                            if time.time() - left_last_click_time < 0.4:
                                pyautogui.doubleClick(button='left', _pause=False)
                                left_last_click_time = 0
                            else:
                                pyautogui.click(button='left', _pause=False)
                                left_last_click_time = time.time()
                        was_left_pinching = False
                        left_drag_started = False
                    if was_right_pinching:
                        if right_drag_started:
                            pyautogui.mouseUp(button='right', _pause=False)
                        else:
                            if time.time() - right_last_click_time < 0.4:
                                pyautogui.doubleClick(button='right', _pause=False)
                                right_last_click_time = 0
                            else:
                                pyautogui.click(button='right', _pause=False)
                                right_last_click_time = time.time()
                        was_right_pinching = False
                        right_drag_started = False
                    last_scroll_y = None
                    last_move_x = None
                    last_move_y = None

                # Yield a tiny bit of CPU but allow full 30 FPS camera speed
                time.sleep(0.01)
        finally:
            if was_left_pinching and left_drag_started:
                pyautogui.mouseUp(button='left', _pause=False)
            if was_right_pinching and right_drag_started:
                pyautogui.mouseUp(button='right', _pause=False)
            hand_detector.close()
            face_detector.close()
                
        # Clean up
        if self.cap:
            self.cap.release()
            self.cap = None
        print("[Gesture] Camera deactivated.")
