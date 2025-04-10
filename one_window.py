import cv2
import mediapipe as mp
import numpy as np
import random
import math
import tkinter as tk
from tkinter import messagebox
import datetime
import os

from PIL import Image, ImageTk  # Для конвертации OpenCV-кадров в изображение Tkinter.

# ------------------------------
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ И НАСТРОЙКИ
# ------------------------------

PLATE_SIZE = 30
ACTIVATION_ANGLE = 5
COOLDOWN_FRAMES = 30
PLATE_COLOR = (0, 255, 0)
TEXT_COLOR = (0, 0, 255)

current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
SQUARES_FILE = "sq.txt"
LOG_FILE = f"dataset/head_movement_log_{current_time}.csv"

yaw_history = []
max_history = 200
yaw_min, yaw_max = -50, 50

graph_width, graph_height = 600, 200
tracker_size = 300
tracker_radius = tracker_size // 2 - 20

last_log_time = datetime.datetime.now()
log_interval = 1.0

mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh(
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
    refine_landmarks=True,
    max_num_faces=1
)

cap = None

nose_coord = (0, 0)
neutral = (0, 0)
current_plate = None
manual_plate = None
cooldown = 0

right_pupil_h = right_pupil_v = 0.5
left_pupil_h = left_pupil_v = 0.5

first_frame = True
squares = []

# Ссылка на второе окно и метки на нём
second_window = None
label_cam = None
label_graph = None
label_tracker = None
label_right_eye = None
label_left_eye = None

# Поля ввода
entry_x = None
entry_y = None

# Флаг, который будет управлять видимостью графиков/трекеров.
graphs_visible = True

# --- Новые константы для размера отображаемой камеры ---
DISPLAY_WIDTH = 1280
DISPLAY_HEIGHT = 960

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# ------------------------------
# ФУНКЦИИ РАБОТЫ С ЛОГАМИ И ФАЙЛАМИ
# ------------------------------

def load_squares():
    squares_local = []
    try:
        with open(SQUARES_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    x, y = map(int, line.split(','))
                    squares_local.append((x, y))
    except FileNotFoundError:
        messagebox.showerror("Ошибка", f"Файл {SQUARES_FILE} не найден!")
        exit()
    except Exception as e:
        messagebox.showerror("Ошибка", f"Ошибка чтения файла: {str(e)}")
        exit()

    if not squares_local:
        messagebox.showerror("Ошибка", f"Файл {SQUARES_FILE} пуст или содержит некорректные данные!")
        exit()
    return squares_local


def log_head_position(roll, pitch, yaw, nose_x, nose_y, direction):
    global last_log_time
    current_time_local = datetime.datetime.now()

    if (current_time_local - last_log_time).total_seconds() >= log_interval:
        timestamp = current_time_local.strftime("%Y-%m-%d %H:%M:%S.%f")
        with open(LOG_FILE, 'a') as f:
            f.write(f"{timestamp},{roll:.2f},{pitch:.2f},{yaw:.2f},{nose_x},{nose_y},{direction}\n")
        last_log_time = current_time_local

# ------------------------------
# ФУНКЦИИ ПОДСЧЁТА УГЛОВ / МАТЕМАТИКА
# ------------------------------

def get_euler_angles(rotation_matrix):
    sy = math.sqrt(rotation_matrix[0, 0] ** 2 + rotation_matrix[1, 0] ** 2)
    singular = sy < 1e-6
    if not singular:
        roll = math.atan2(rotation_matrix[2, 1], rotation_matrix[2, 2])
        pitch = math.atan2(-rotation_matrix[2, 0], sy)
        yaw = math.atan2(rotation_matrix[1, 0], rotation_matrix[0, 0])
    else:
        roll = math.atan2(-rotation_matrix[1, 2], rotation_matrix[1, 1])
        pitch = math.atan2(-rotation_matrix[2, 0], sy)
        yaw = 0
    return np.degrees(roll), np.degrees(pitch), np.degrees(yaw)


def calculate_head_pose(landmarks, frame_shape):
    image_points = np.array([
        (landmarks[1].x, landmarks[1].y),
        (landmarks[33].x, landmarks[33].y),
        (landmarks[263].x, landmarks[263].y),
        (landmarks[61].x, landmarks[61].y),
        (landmarks[291].x, landmarks[291].y),
        (landmarks[152].x, landmarks[152].y)
    ], dtype=np.float64) * frame_shape[::-1]

    model_points = np.array([
        (0.0, 0.0, 0.0),
        (-0.15, 0.45, -0.1),
        (0.15, 0.45, -0.1),
        (-0.2, -0.3, -0.1),
        (0.2, -0.3, -0.1),
        (0.0, -0.5, 0.0)
    ], dtype=np.float64)

    focal_length = frame_shape[1]
    center = (frame_shape[1] / 2, frame_shape[0] / 2)
    camera_matrix = np.array([
        [focal_length, 0, center[0]],
        [0, focal_length, center[1]],
        [0, 0, 1]
    ], dtype=np.float64)

    dist_coeffs = np.zeros((4, 1))
    _, rotation_vec, _ = cv2.solvePnP(
        model_points,
        image_points,
        camera_matrix,
        dist_coeffs,
        flags=cv2.SOLVEPNP_ITERATIVE
    )
    rmat, _ = cv2.Rodrigues(rotation_vec)
    return get_euler_angles(rmat)


def calculate_pupil_position(inner_corner, outer_corner, pupil):
    inner = np.array(inner_corner)
    outer = np.array(outer_corner)
    pup = np.array(pupil)
    eye_vector = outer - inner
    eye_length = np.linalg.norm(eye_vector)
    if eye_length == 0:
        return 0.5
    pupil_vector = pup - inner
    projection = np.dot(pupil_vector, eye_vector) / eye_length
    pos = projection / eye_length
    return pos


# ------------------------------
# ФУНКЦИИ ДЛЯ ОТРИСОВКИ
# ------------------------------

def create_eye_tracker2(norm_h, norm_v, eye_side="Right"):
    tracker_width = 400
    tracker_height = 200
    tracker = np.ones((tracker_height, tracker_width, 3), dtype=np.uint8) * 255

    margin = 20
    cv2.rectangle(tracker, (margin, margin), (tracker_width - margin, tracker_height - margin), (0, 0, 0), 2)

    base_y = tracker_height // 2
    cv2.line(tracker, (margin, base_y), (tracker_width - margin, base_y), (0, 0, 0), 2)

    pos_x = int(margin + norm_h * (tracker_width - 2 * margin))
    pos_y = int(base_y + norm_v * ((tracker_height // 2) - margin))

    cv2.circle(tracker, (pos_x, pos_y), 10, (255, 0, 0), -1)
    cv2.putText(tracker, f"{eye_side} Eye", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    cv2.putText(tracker, f"H:{norm_h:.2f} V:{norm_v:.2f}", (10, tracker_height - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

    return tracker


def generate_new_plate(h_img, w_img):
    if not squares:
        return None
    x, y = random.choice(squares)
    position = 'left' if x < w_img // 2 else 'right'
    return (x, y, position, False)


# ------------------------------
# ОСНОВНАЯ ЛОГИКА ТРЕКА, ОБНОВЛЕНИЕ КАДРОВ
# ------------------------------

def update_frame():
    global cap, current_plate, manual_plate, cooldown
    global nose_coord, first_frame, neutral
    global right_pupil_h, right_pupil_v, left_pupil_h, left_pupil_v
    global yaw_history

    if cap is None or not cap.isOpened():
        second_window.after(30, update_frame)
        return

    success, frame = cap.read()
    if not success:
        second_window.after(30, update_frame)
        return

    frame = cv2.flip(frame, 1)
    h, w = frame.shape[:2]

    # Распознавание Mediapipe работает на оригинальном размере:
    image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = face_mesh.process(image_rgb)

    if manual_plate is not None:
        x_mp, y_mp = manual_plate
        current_plate = (x_mp, y_mp, 'left' if x_mp < w // 2 else 'right', False)
        manual_plate = None
    elif current_plate is None and cooldown == 0:
        current_plate = generate_new_plate(h, w)

    if results.multi_face_landmarks:
        landmarks = results.multi_face_landmarks[0].landmark
        roll, pitch, yaw = calculate_head_pose(landmarks, (h, w))

        yaw_history.append(yaw)
        if len(yaw_history) > max_history:
            yaw_history.pop(0)

        nose = landmarks[1]
        nose_coord = (int(nose.x * w), int(nose.y * h))
        cv2.circle(frame, nose_coord, 4, (0, 255, 255), -1)

        if yaw < -10:
            direction = "left"
        elif yaw > 10:
            direction = "right"
        else:
            direction = "center"

        log_head_position(roll, pitch, yaw, nose_coord[0], nose_coord[1], direction)

        if current_plate:
            x_cp, y_cp, pos_cp, _ = current_plate
            if (pos_cp == 'left' and yaw < -ACTIVATION_ANGLE) or (pos_cp == 'right' and yaw > ACTIVATION_ANGLE):
                current_plate = None
                cooldown = COOLDOWN_FRAMES

        length = 100
        angle_rad = math.radians(yaw)
        end_point = (nose_coord[0] + int(length * math.sin(angle_rad)), nose_coord[1])
        overlay = frame.copy()
        cv2.arrowedLine(overlay, nose_coord, end_point, (255, 0, 0), thickness=2)
        alpha = 0.3
        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

        # Глаза
        right_inner = landmarks[133]
        right_outer = landmarks[33]
        right_pupil = landmarks[468]
        rx_inner = (int(right_inner.x * w), int(right_inner.y * h))
        rx_outer = (int(right_outer.x * w), int(right_outer.y * h))
        rx_pupil = (int(right_pupil.x * w), int(right_pupil.y * h))

        left_inner = landmarks[362]
        left_outer = landmarks[263]
        left_pupil = landmarks[473]
        lx_inner = (int(left_inner.x * w), int(left_inner.y * h))
        lx_outer = (int(left_outer.x * w), int(left_outer.y * h))
        lx_pupil = (int(left_pupil.x * w), int(left_pupil.y * h))

        cv2.circle(frame, rx_inner, 3, (0, 0, 255), -1)
        cv2.circle(frame, rx_outer, 3, (0, 0, 255), -1)
        cv2.circle(frame, rx_pupil, 3, (255, 0, 0), -1)
        cv2.line(frame, rx_inner, rx_outer, (0, 0, 255), 1)

        cv2.circle(frame, lx_inner, 3, (0, 0, 255), -1)
        cv2.circle(frame, lx_outer, 3, (0, 0, 255), -1)
        cv2.circle(frame, lx_pupil, 3, (255, 0, 0), -1)
        cv2.line(frame, lx_inner, lx_outer, (0, 0, 255), 1)

        right_pupil_h = calculate_pupil_position(rx_inner, rx_outer, rx_pupil)
        left_pupil_h = calculate_pupil_position(lx_inner, lx_outer, lx_pupil)

        right_eye_upper = landmarks[159]
        right_eye_lower = landmarks[145]
        rx_upper = (int(right_eye_upper.x * w), int(right_eye_upper.y * h))
        rx_lower = (int(right_eye_lower.x * w), int(right_eye_lower.y * h))
        eye_height_right = abs(rx_upper[1] - rx_lower[1])
        right_baseline = (rx_upper[1] + rx_lower[1]) / 2.0
        right_pupil_v = (rx_pupil[1] - right_baseline) / (eye_height_right if eye_height_right != 0 else 1)

        left_eye_upper = landmarks[386]
        left_eye_lower = landmarks[374]
        lx_upper = (int(left_eye_upper.x * w), int(left_eye_upper.y * h))
        lx_lower = (int(left_eye_lower.x * w), int(left_eye_lower.y * h))
        eye_height_left = abs(lx_upper[1] - lx_lower[1])
        left_baseline = (lx_upper[1] + lx_lower[1]) / 2.0
        left_pupil_v = (lx_pupil[1] - left_baseline) / (eye_height_left if eye_height_left != 0 else 1)

        cv2.putText(frame, f"Right pos: {right_pupil_h:.2f}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, TEXT_COLOR, 2)
        cv2.putText(frame, f"Left pos: {left_pupil_h:.2f}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, TEXT_COLOR, 2)
        cv2.putText(frame, f"Roll: {roll:.1f}", (w - 150, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, TEXT_COLOR, 2)
        cv2.putText(frame, f"Pitch: {pitch:.1f}", (w - 150, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, TEXT_COLOR, 2)
        cv2.putText(frame, f"Yaw: {yaw:.1f}", (w - 150, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, TEXT_COLOR, 2)

    if current_plate:
        x_cp, y_cp, _, _ = current_plate
        cv2.rectangle(frame, (x_cp, y_cp), (x_cp + PLATE_SIZE, y_cp + PLATE_SIZE), PLATE_COLOR, 3)

    if cooldown > 0:
        cooldown -= 1
        cv2.putText(frame, f"Next: {cooldown // 10 + 1}", (w // 2 - 30, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, TEXT_COLOR, 2)

    # График Yaw
    graph = np.ones((graph_height, graph_width, 3), dtype=np.uint8) * 255
    if len(yaw_history) > 1:
        for i in range(1, len(yaw_history)):
            x1 = int((i - 1) * graph_width / max_history)
            x2 = int(i * graph_width / max_history)
            y1 = graph_height - int((yaw_history[i - 1] - yaw_min) * graph_height / (yaw_max - yaw_min))
            y2 = graph_height - int((yaw_history[i] - yaw_min) * graph_height / (yaw_max - yaw_min))
            cv2.line(graph, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(graph, "Yaw (deg)", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)

    if neutral == (0, 0):
        neutral = (w // 2, h // 2)

    offset_x = nose_coord[0] - neutral[0]
    offset_y = nose_coord[1] - neutral[1]
    norm_offset_x = int((offset_x / (w / 2)) * tracker_radius)
    norm_offset_y = int((offset_y / (h / 2)) * tracker_radius)

    tracker_img = np.ones((tracker_size, tracker_size, 3), dtype=np.uint8) * 255
    center_tracker = (tracker_size // 2, tracker_size // 2)
    cv2.circle(tracker_img, center_tracker, tracker_radius, (0, 0, 0), 2)
    tracker_point = (center_tracker[0] + norm_offset_x, center_tracker[1] + norm_offset_y)
    cv2.circle(tracker_img, tracker_point, 10, (0, 0, 255), -1)
    cv2.putText(tracker_img, "Nose", (tracker_point[0] - 30, tracker_point[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

    right_eye_tracker = create_eye_tracker2(right_pupil_h, right_pupil_v, "Right")
    left_eye_tracker = create_eye_tracker2(left_pupil_h, left_pupil_v, "Left")

    # --- Увеличиваем кадр для вывода (DISPLAY_WIDTH x DISPLAY_HEIGHT) ---
    resized_frame = cv2.resize(frame, (DISPLAY_WIDTH, DISPLAY_HEIGHT), interpolation=cv2.INTER_LINEAR)

    # Конвертируем в BGR -> PIL
    frame_bgr = cv2.cvtColor(resized_frame, cv2.COLOR_RGB2BGR)
    img_cam = Image.fromarray(frame_bgr)
    imgtk_cam = ImageTk.PhotoImage(image=img_cam)
    label_cam.config(image=imgtk_cam)
    label_cam.image = imgtk_cam

    # Остальные изображения оставляем в исходном размере,
    # но при желании их тоже можно увеличить (по аналогии):
    img_graph = Image.fromarray(graph)
    imgtk_graph = ImageTk.PhotoImage(image=img_graph)
    label_graph.config(image=imgtk_graph)
    label_graph.image = imgtk_graph

    img_tracker = Image.fromarray(tracker_img)
    imgtk_tracker = ImageTk.PhotoImage(image=img_tracker)
    label_tracker.config(image=imgtk_tracker)
    label_tracker.image = imgtk_tracker

    img_right_eye = Image.fromarray(right_eye_tracker)
    imgtk_right_eye = ImageTk.PhotoImage(image=img_right_eye)
    label_right_eye.config(image=imgtk_right_eye)
    label_right_eye.image = imgtk_right_eye

    img_left_eye = Image.fromarray(left_eye_tracker)
    imgtk_left_eye = ImageTk.PhotoImage(image=img_left_eye)
    label_left_eye.config(image=imgtk_left_eye)
    label_left_eye.image = imgtk_left_eye

    second_window.after(10, update_frame)

# ------------------------------
# ФУНКЦИИ ДЛЯ УПРАВЛЕНИЯ ОКНАМИ
# ------------------------------

def add_square():
    global manual_plate
    try:
        x_val = int(entry_x.get())
        y_val = int(entry_y.get())
        manual_plate = (x_val, y_val)
        print("Added manual square:", x_val, y_val)
    except Exception as e:
        print("Invalid input:", e)


def finish_test():
    global cap
    if cap is not None:
        cap.release()
        cap = None
    cv2.destroyAllWindows()
    root.destroy()


def toggle_graphs():
    global graphs_visible

    if graphs_visible:
        label_graph.grid_remove()
        label_tracker.grid_remove()
        label_right_eye.grid_remove()
        label_left_eye.grid_remove()
        toggle_btn.config(text="Показать графики")
        graphs_visible = False
    else:
        label_graph.grid()
        label_tracker.grid()
        label_right_eye.grid()
        label_left_eye.grid()
        toggle_btn.config(text="Скрыть графики")
        graphs_visible = True


def start_test():
    global cap, second_window
    global label_cam, label_graph, label_tracker
    global label_right_eye, label_left_eye, entry_x, entry_y
    global toggle_btn

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        messagebox.showerror("Ошибка", "Камера не найдена!")
        root.destroy()
        return

    second_window = tk.Toplevel(root)
    second_window.title("Окно испытания")
    second_window.geometry("2200x1400")

    for col in range(3):
        second_window.grid_columnconfigure(col, weight=1)

    label_graph = tk.Label(second_window)
    label_graph.grid(row=0, column=0, padx=5, pady=5, sticky="n")

    label_cam = tk.Label(second_window)
    label_cam.grid(row=0, column=1, padx=5, pady=5, sticky="n")

    label_tracker = tk.Label(second_window)
    label_tracker.grid(row=0, column=2, padx=5, pady=5, sticky="n")

    label_right_eye = tk.Label(second_window)
    label_right_eye.grid(row=1, column=0, padx=5, pady=5, sticky="n")

    label_left_eye = tk.Label(second_window)
    label_left_eye.grid(row=1, column=1, padx=5, pady=5, sticky="n")

    frm_sq = tk.Frame(second_window)
    frm_sq.grid(row=2, column=0, columnspan=3, pady=5)

    tk.Label(frm_sq, text="X:").grid(row=0, column=0)
    entry_x = tk.Entry(frm_sq, width=10)
    entry_x.grid(row=0, column=1)

    tk.Label(frm_sq, text="Y:").grid(row=1, column=0)
    entry_y = tk.Entry(frm_sq, width=10)
    entry_y.grid(row=1, column=1)

    btn_add_square = tk.Button(frm_sq, text="Add Square", command=add_square)
    btn_add_square.grid(row=2, column=0, columnspan=2, pady=5)

    toggle_btn = tk.Button(second_window, text="Скрыть графики", command=toggle_graphs)
    toggle_btn.grid(row=3, column=0, padx=5, pady=5)

    btn_finish = tk.Button(second_window, text="Закончить испытание", command=finish_test)
    btn_finish.grid(row=3, column=1, padx=5, pady=5)

    update_frame()

# ------------------------------
# СТАРТ ПРИЛОЖЕНИЯ
# ------------------------------

if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, 'w') as f:
        f.write("timestamp,roll,pitch,yaw,nose_x,nose_y,direction\n")

squares = load_squares()

root = tk.Tk()
root.title("Стартовое окно")

start_button = tk.Button(root, text="Начать испытание", font=("Arial", 14), command=start_test)
start_button.pack(padx=20, pady=20)

root.mainloop()
