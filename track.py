import argparse

import sys
import numpy as np
from pathlib import Path
import torch
import torch.backends.cudnn as cudnn
from numpy import random
import pandas as pd
import geopandas as gpd
import fiona
from geopy import distance
import os

from yolov7.models.experimental import attempt_load
from yolov7.utils.datasets import LoadImages, LoadStreams
from yolov7.utils.general import (check_img_size, non_max_suppression, scale_coords, check_requirements, cv2,
                                  check_imshow, xyxy2xywh, increment_path, strip_optimizer, colorstr, check_file)
from yolov7.utils.torch_utils import select_device, time_synchronized
from yolov7.utils.plots import plot_one_box
from strong_sort.utils.parser import get_config
from strong_sort.strong_sort import StrongSORT
from complete_data.utils import complete_kml, complete_vid

import warnings

warnings.filterwarnings("ignore")

# limit the number of cpus used by high performance libraries
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

FILE = Path(__file__).resolve()
ROOT = FILE.parents[0]  # yolov5 strongsort root directory
WEIGHTS = ROOT / 'weights'

if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # add ROOT to PATH

if str(ROOT / 'yolov7') not in sys.path:
    sys.path.append(str(ROOT / 'yolov7'))  # add yolov7 ROOT to PATH

if str(ROOT / 'strong_sort') not in sys.path:
    sys.path.append(str(ROOT / 'strong_sort'))  # add strong_sort ROOT to PATH

ROOT = Path(os.path.relpath(ROOT, Path.cwd()))  # relative

VID_FORMATS = ('asf', 'avi', 'gif', 'm4v', 'mkv', 'mov', 'mp4', 'mpeg', 'mpg', 'ts', 'wmv')  # include video suffixes


@torch.no_grad()
def run(
        source='0',
        yolo_weights=WEIGHTS / 'best.pt',  # model.pt path(s),
        strong_sort_weights=WEIGHTS / 'osnet_x0_25_msmt17.pt',  # model.pt path,
        config_strongsort=ROOT / 'strong_sort/configs/strong_sort.yaml',
        imgsz=(640, 640),  # inference size (height, width)
        conf_thres=0.25,  # confidence threshold
        iou_thres=0.45,  # NMS IOU threshold
        max_det=1000,  # maximum detections per image
        device='cpu',  # cuda device, i.e. 0 or 0,1,2,3 or cpu
        show_vid=True,  # show results
        save_txt=False,  # save results to *.txt
        save_conf=False,  # save confidences in --save-txt labels
        save_crop=False,  # save cropped prediction boxes
        save_vid=True,  # save confidences in --save-txt labels
        nosave=False,  # do not save images/videos
        classes=None,  # filter by class: --class 0, or --class 0 2 3
        agnostic_nms=False,  # class-agnostic NMS
        augment=False,  # augmented inference
        visualize=False,  # visualize features
        update=False,  # update all models
        project=ROOT / 'Outputs',  # save results to project/name
        name='run',  # save results to project/name
        exist_ok=False,  # existing project/name ok, do not increment
        line_thickness=2,  # bounding box thickness (pixels)
        hide_labels=False,  # hide labels
        hide_conf=False,  # hide confidences
        hide_class=False,  # hide IDs
        half=False,  # use FP16 half-precision inference
        dnn=False,  # use OpenCV DNN for ONNX inference
        kml_path='demo.kml',  # Archivo kml a analizar
        square_img_size= 1280,
):
    source = str(source)
    save_img = not nosave and not source.endswith('.txt')  # save inference images
    is_file = Path(source).suffix[1:] in VID_FORMATS
    is_url = source.lower().startswith(('rtsp://', 'rtmp://', 'http://', 'https://'))
    webcam = source.isnumeric() or source.endswith('.txt') or (is_url and not is_file)
    if is_url and is_file:
        source = check_file(source)  # download

    # Directories
    name_path = os.path.basename(kml_path).split('.')[0]
    project = project / name_path

    if not isinstance(yolo_weights, list):  # single yolo model
        exp_name = yolo_weights.stem
    elif type(yolo_weights) is list and len(yolo_weights) == 1:  # single models after --yolo_weights
        exp_name = Path(yolo_weights[0]).stem
        yolo_weights = Path(yolo_weights[0])
    else:  # multiple models after --yolo_weights
        exp_name = 'ensemble'

    exp_name = name if name else exp_name + "_" + strong_sort_weights.stem
    save_dir = increment_path(Path(project) / exp_name, exist_ok=exist_ok)  # increment run
    save_dir = Path(save_dir)
    (save_dir / 'tracks' if save_txt else save_dir).mkdir(parents=True, exist_ok=True)  # make dir

    # Load model
    device = select_device(device)
    WEIGHTS.mkdir(parents=True, exist_ok=True)
    model = attempt_load(Path(yolo_weights), map_location=device)  # load FP32 model
    names = model.names
    stride = model.stride.max()  # model stride
    imgsz = check_img_size(imgsz[0], s=stride.cpu().numpy())  # check image size

    # Check Video
    del_vid_path = complete_vid(source, save_dir, square_img_size)

    # Dataloader
    if webcam:
        show_vid = check_imshow()
        cudnn.benchmark = True  # set True to speed up constant image size inference
        dataset = LoadStreams(del_vid_path, img_size=imgsz, stride=stride.cpu().numpy())
        nr_sources = 1
    else:
        dataset = LoadImages(del_vid_path, img_size=imgsz, stride=stride)
        nr_sources = 1
    vid_path, vid_writer, txt_path = [None] * nr_sources, [None] * nr_sources, [None] * nr_sources

    # initialize StrongSORT
    cfg = get_config()
    cfg.merge_from_file(config_strongsort)

    # Create as many strong sort instances as there are video sources
    strongsort_list = []
    for i in range(nr_sources):
        strongsort_list.append(
            StrongSORT(
                strong_sort_weights,
                device,
                half,
                max_dist=cfg.STRONGSORT.MAX_DIST,
                max_iou_distance=cfg.STRONGSORT.MAX_IOU_DISTANCE,
                max_age=cfg.STRONGSORT.MAX_AGE,
                n_init=cfg.STRONGSORT.N_INIT,
                nn_budget=cfg.STRONGSORT.NN_BUDGET,
                mc_lambda=cfg.STRONGSORT.MC_LAMBDA,
                ema_alpha=cfg.STRONGSORT.EMA_ALPHA,

            )
        )
        strongsort_list[i].model.warmup()

    outputs = [None] * nr_sources

    colors = [[random.randint(0, 255) for _ in range(3)] for _ in names]

    # initiate KML file reading
    gpd.io.file.fiona.drvsupport.supported_drivers['KML'] = 'rw'
    geopd_df = gpd.read_file(kml_path, driver='KML')
    geo_df = pd.DataFrame(geopd_df)

    # Extract latitude and longitude from the KML geometry column
    geo_df['Latitude'] = geo_df.geometry.apply(lambda p: p.y)
    geo_df['Longitude'] = geo_df.geometry.apply(lambda p: p.x)
    geo_df['Altitude'] = geo_df.geometry.apply(lambda p: p.z)

    # Create the completed DF of the KML file
    complete_df = complete_kml(geo_df, dataset.nframes)

    # Creaci??n de distancias
    dist_rec = []
    past_lat = complete_df.Latitude[0]
    past_long = complete_df.Longitude[0]
    past_alt = complete_df.Altitude[0]
    past_point = [past_lat, past_long, past_alt]
    past_dist = 0

    # Create dictionaries to retrieve info based on item IDs

    dict_frame = {}
    dict_class = {}
    dict_confidence = {}
    dict_imgs = {}
    dict_plots = {}

    # Run tracking
    dt, seen = [0.0, 0.0, 0.0, 0.0], 0  # Diferencia temporal en etapas y elementos vistos por img
    curr_frames, prev_frames = [None] * nr_sources, [None] * nr_sources

    # path -> video location
    # im -> frame reshaped a 3,640,640
    # im0s -> frame original 3,1280,1280
    # vid_cap -> no idea
    for frame_idx, (path, im, im0s, vid_cap) in enumerate(dataset):

        dict_imgs.setdefault(frame_idx + 1, im0s.copy())  #
        s = ''
        t1 = time_synchronized()
        im = torch.from_numpy(im).to(device)
        im = im.half() if half else im.float()  # uint8 to fp16/32
        im /= 255.0  # 0 - 255 to 0.0 - 1.0
        if len(im.shape) == 3:
            im = im[None]  # expand for batch dim -> se pasa de 3x640x640 a 1x3x640x640

        t2 = time_synchronized()
        dt[0] += t2 - t1

        # Inference
        visualize = increment_path(save_dir / Path(path[0]).stem, mkdir=True) if visualize else False
        pred = model(im)
        # pred[0].shape[2]-5 -> n?? classes

        t3 = time_synchronized()
        dt[1] += t3 - t2

        # Apply NMS
        pred = non_max_suppression(pred[0], conf_thres, iou_thres, classes, agnostic_nms)
        dt[2] += time_synchronized() - t3

        # Process detections
        for i, det in enumerate(pred):  # detections per image

            seen += 1
            if webcam:  # nr_sources >= 1
                p, im0, _ = path[i], im0s[i].copy(), dataset.count
                p = Path(p)  # to Path
                s += f'{i}: '
                txt_file_name = p.name
                save_path = str(save_dir / source)  # im.jpg, vid.mp4, ...

            else:
                p, im0, _ = path, im0s.copy(), getattr(dataset, 'frame', 0)
                p = Path(p)  # to Path
                # video file
                if source.endswith(VID_FORMATS):
                    txt_file_name = p.stem
                    save_path = str(save_dir / source)  # im.jpg, vid.mp4, ...
                # folder with imgs
                else:
                    txt_file_name = p.parent.name  # get folder name containing current img
                    save_path = str(save_dir / source)  # im.jpg, vid.mp4, ...

            curr_frames[i] = im0

            txt_path = str(save_dir / 'tracks' / txt_file_name)  # im.txt
            s += '%gx%g ' % im.shape[2:]  # print string
            imc = im0.copy() if save_crop else im0  # for save_crop

            if cfg.STRONGSORT.ECC:  # camera motion compensation
                strongsort_list[i].tracker.camera_update(prev_frames[i], curr_frames[i])

            if det is not None and len(det):
                # Rescale boxes from img_size to im0 size
                det_og = det[:, :4].round()
                det[:, :4] = scale_coords(im.shape[2:], det[:, :4], im0.shape).round()

                # Print results
                for c in det[:, -1].unique():
                    n = (det[:, -1] == c).sum()  # detections per class
                    s += f"{n} of {names[int(c)]}{'s' * (n > 1)}, "  # add to string

                xywhs = xyxy2xywh(det[:, 0:4])
                confs = det[:, 4]
                clss = det[:, 5]

                # pass detections to strongsort
                t4 = time_synchronized()
                outputs[i] = strongsort_list[i].update(xywhs.cpu(), confs.cpu(), clss.cpu(), im0)
                t5 = time_synchronized()
                dt[3] += t5 - t4

                # draw boxes for visualization and save info
                if len(outputs[i]) > 0:
                    # print([[frame_idx + 1, tracks.track_id, tracks.class_id.item(), tracks.conf.item()] for tracks in
                    # strongsort_list[i].tracker.tracks if tracks.is_confirmed()])

                    for j, (output, conf) in enumerate(zip(outputs[i], confs)):  # (output[6]==conf) No change, it works

                        bboxes_og = det[j,0:4]
                        bboxes = output[0:4]
                        id = int(output[4])
                        cls = int(output[5])
                        conf = round(conf.item(), 2)

                        # Get info into the dictionaries

                        dict_frame.setdefault(str(id), [])  # frames
                        dict_frame[str(id)].append(frame_idx + 1)

                        dict_class.setdefault(str(id), names[cls])  # classes

                        dict_confidence.setdefault(str(id), [])  # confidence
                        dict_confidence[str(id)].append(conf)

                        dict_plots.setdefault(str(id), [])  # guardado de bbox de objetos detectados
                        dict_plots[str(id)].append(bboxes)

                        if save_txt:
                            # to MOT format
                            bbox_left = output[0]
                            bbox_top = output[1]
                            bbox_w = output[2] - output[0]
                            bbox_h = output[3] - output[1]
                            # Write MOT compliant results to file
                            with open(txt_path + '.txt', 'a') as f:
                                f.write(('%g ' * 10 + '\n') % (frame_idx + 1, id, bbox_left,  # MOT format
                                                               bbox_top, bbox_w, bbox_h, -1, -1, -1, i))

                        if save_vid or save_crop or show_vid:  # Add bbox to image

                            label = None if hide_labels else (f'{id} {names[cls]}' if hide_conf else \
                                                                  (
                                                                      f'{id} {conf:.2f}' if hide_class else f'{id} {names[cls]} {conf:.2f}'))

                            plot_one_box(bboxes, im0, label=label, color=colors[int(cls)], line_thickness=2)

                            # if save_crop:
                            # txt_file_name = txt_file_name if (isinstance(path, list) and len(path) > 1) else ''
                            # save_one_box(bboxes, imc, file=save_dir / 'crops' / txt_file_name / names[c] / f'{id}' / f'{p.stem}.jpg', BGR=True)}

                print(f'{s}Done. YOLO:({t3 - t2:.3f}s), StrongSORT:({t5 - t4:.3f}s)')

            else:
                strongsort_list[i].increment_ages()
                print('No detections')

            # Stream results
            if show_vid:
                cv2.imshow(str(p), im0)
                cv2.waitKey(1)  # 1 millisecond

            # Save results (image with detections)
            if save_vid:
                if vid_path[i] != save_path:  # new video
                    vid_path[i] = save_path
                    if isinstance(vid_writer[i], cv2.VideoWriter):
                        vid_writer[i].release()  # release previous video writer
                    if vid_cap:  # video
                        fps = vid_cap.get(cv2.CAP_PROP_FPS)
                        w = int(vid_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                        h = int(vid_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    else:  # stream
                        fps, w, h = 30, im0.shape[1], im0.shape[0]
                    save_path = str(Path(save_path).with_suffix('.mp4'))  # force *.mp4 suffix on results videos
                    vid_writer[i] = cv2.VideoWriter(save_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
                vid_writer[i].write(im0)

            prev_frames[i] = curr_frames[i]

        # Update curr location
        curr_lat = complete_df.Latitude[frame_idx]
        curr_long = complete_df.Longitude[frame_idx]
        curr_alt = complete_df.Altitude[frame_idx]
        curr_point = [curr_lat, curr_long, curr_alt]

        # Calculate distance traveled
        distance_2d = distance.distance(past_point[:2], curr_point[:2]).m
        distance_3d = np.sqrt(distance_2d ** 2 + (past_point[2] - curr_point[2]) ** 2)
        dist_rec.append(distance_3d + past_dist)

        # Update past location
        past_lat = complete_df.Latitude[frame_idx]
        past_long = complete_df.Longitude[frame_idx]
        past_alt = complete_df.Altitude[frame_idx]
        past_dist = distance_3d + past_dist
        past_point = [past_lat, past_long, past_alt]

    # Create directory for images
    if not os.path.isdir(save_dir / 'Imgs'):
        # not present then create it.
        os.makedirs(save_dir / 'Imgs')

    # Create DF
    cols = ['ID_Objeto', 'ID_Fotograma', 'Dist Met (Km)', 'Clase', 'Max seguridad', 'Min seguridad', 'Latitud',
            'Longitud']
    df_out = pd.DataFrame(columns=cols)  # To add a row -> df_out.loc[len(df_out)] = Row_in en formato lista

    IDS = dict_class.keys()
    for id_obj in IDS:
        clase = dict_class[id_obj]  # clase del id_obj
        max_conf = max(dict_confidence[id_obj])
        min_conf = min(dict_confidence[id_obj])
        idx_max_conf = dict_confidence[id_obj].index(max_conf)  # ??ndice de max conf
        id_frame = dict_frame[id_obj][idx_max_conf]  # frame con el max conf, guardado como (idx_frame + 1)
        last_frame = dict_frame[id_obj][-1]  # ??ltima vista del objeto para Lat and Long
        lat = complete_df.iloc[last_frame - 1].Latitude
        long = complete_df.iloc[last_frame - 1].Longitude
        trav_dist = round(dist_rec[last_frame - 1] / 1000, 4)

        # Save info in DF
        row_list = [int(id_obj), id_frame, trav_dist, clase, max_conf, min_conf, lat, long]
        df_out.loc[len(df_out)] = row_list

        # Save imgs
        bb = dict_plots[id_obj][idx_max_conf]  # bounding box del obj con mayor conf
        img_2save = dict_imgs[id_frame]  # img del frame con mayor conf

        label = f'{id_obj} {clase} {max_conf:.2f}'

        plot_one_box(bb, img_2save, label=label, color=[255, 0, 255], line_thickness=3)

        file_name = f'{id_obj}_ID.jpg'
        img_path = save_dir / 'Imgs' / file_name
        cv2.imwrite(str(img_path), img_2save)

    df_out.to_excel(save_dir / f'{name_path}.xlsx')
    os.remove(del_vid_path)

    # Print results
    t = tuple(x / seen * 1E3 for x in dt)  # speeds per image
    print(
        f'Speed: %.1fms pre-process, %.1fms inference, %.1fms NMS, %.1fms strong sort update per image at shape {(1, 3, imgsz, imgsz)}' % t)
    if save_txt or save_vid:
        s = f"\n{len(list(save_dir.glob('tracks/*.txt')))} tracks saved to {save_dir / 'tracks'}" if save_txt else ''
        print(f"Results saved to {colorstr('bold', save_dir)}{s}")
    if update:
        strip_optimizer(yolo_weights)  # update model (to fix SourceChangeWarning)


def parse_opt():
    parser = argparse.ArgumentParser()
    parser.add_argument('--yolo-weights', nargs='+', type=str, default=WEIGHTS / 'best.pt', help='model.pt path(s)')
    parser.add_argument('--strong-sort-weights', type=str, default=WEIGHTS / 'osnet_x0_25_msmt17.pt')
    parser.add_argument('--config-strongsort', type=str, default='strong_sort/configs/strong_sort.yaml')
    parser.add_argument('--source', type=str, default='demo.avi', help='file/dir/URL/glob, 0 for webcam')
    parser.add_argument('--imgsz', '--img', '--img-size', nargs='+', type=int, default=[640], help='inference size h,w')
    parser.add_argument('--conf-thres', type=float, default=0.5, help='confidence threshold')
    parser.add_argument('--iou-thres', type=float, default=0.5, help='NMS IoU threshold')
    parser.add_argument('--max-det', type=int, default=1000, help='maximum detections per image')
    parser.add_argument('--device', default='cpu', help='cuda device, i.e. 0 or 0,1,2,3 or cpu')
    parser.add_argument('--show-vid', action='store_true', help='display tracking video results')
    parser.add_argument('--save-txt', action='store_true', help='save results to *.txt')
    parser.add_argument('--save-conf', action='store_true', help='save confidences in --save-txt labels')
    parser.add_argument('--save-crop', action='store_true', help='save cropped prediction boxes')
    parser.add_argument('--save-vid', action='store_false', help='save video tracking results')
    parser.add_argument('--nosave', action='store_true', help='do not save images/videos')
    # class 0 is person, 1 is bycicle, 2 is car... 79 is oven
    parser.add_argument('--classes', nargs='+', type=int, help='filter by class: --classes 0, or --classes 0 2 3')
    parser.add_argument('--agnostic-nms', action='store_true', help='class-agnostic NMS')
    parser.add_argument('--augment', action='store_true', help='augmented inference')
    parser.add_argument('--visualize', action='store_true', help='visualize features')
    parser.add_argument('--update', action='store_true', help='update all models')
    parser.add_argument('--project', default=ROOT / 'Outputs', help='save results to project/name')
    parser.add_argument('--name', default='run', help='save results to project/name')
    parser.add_argument('--exist-ok', action='store_true', help='existing project/name ok, do not increment')
    parser.add_argument('--line-thickness', default=3, type=int, help='bounding box thickness (pixels)')
    parser.add_argument('--hide-labels', default=False, action='store_true', help='hide labels')
    parser.add_argument('--hide-conf', default=False, action='store_true', help='hide confidences')
    parser.add_argument('--hide-class', default=False, action='store_true', help='hide IDs')
    parser.add_argument('--half', action='store_true', help='use FP16 half-precision inference')
    parser.add_argument('--dnn', action='store_true', help='use OpenCV DNN for ONNX inference')
    parser.add_argument('--kml-path', type=str, default='demo.kml', help='path archivo kml')
    parser.add_argument('--square-img-size', type=int, default=1280, help='tama??o de outputs cuadrados')

    opt = parser.parse_args()
    opt.imgsz *= 2 if len(opt.imgsz) == 1 else 1  # expand

    return opt


def main(opt):
    check_requirements(requirements=ROOT / 'requirements.txt', exclude=('tensorboard', 'thop'))
    run(**vars(opt))


if __name__ == "__main__":
    opt = parse_opt()
    main(opt)
