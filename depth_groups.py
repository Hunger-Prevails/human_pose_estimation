import os
import cv2
import json
import boxlib
import copy
import scipy
import utils
import glob
import pickle5 as pickle
import numpy as np
import cameralib
import multiprocessing
import matplotlib.pyplot as plt


def make_sample(sample, cameras, image, args):
	'''
	Args:
		sample: dict(skeleton = pose_coord, valid = valid, frame = frame, video = video_id, bbox = bbox)
		cameras: tuple(color_cam, depth_cam)
	'''
	color_cam, depth_cam = cameras

	box_center = boxlib.center(sample['bbox'])

	depth_bbox = utils.transfer_bbox(sample['bbox'], color_cam, depth_cam)

	sine = np.sin(np.pi / 6)
	cosine = np.cos(np.pi / 6)

	expand_shape = np.array([[cosine, sine], [sine, cosine]]) @ sample['bbox'][2:, np.newaxis]
	expand_side = np.max(expand_shape)

	scale_factor = min(args.side_in / np.max(sample['bbox'][2:]) / args.random_zoom, 1.0)

	dest_side = int(np.round(expand_side * scale_factor))

	new_cam = copy.deepcopy(color_cam)
	new_cam.shift_to_center(box_center, (expand_side, expand_side))
	new_cam.scale_output(scale_factor)

	new_bbox = cameralib.reproject_points(sample['bbox'][None, :2], color_cam, new_cam)[0]

	new_bbox = np.concatenate([new_bbox, sample['bbox'][2:] * scale_factor])

	new_path = os.path.join(args.down_path, str(sample['frame']) + '.jpg')

	if not os.path.exists(new_path):

		new_image = cameralib.reproject_image(image, color_cam, new_cam, (dest_side, dest_side))

		plt.imsave(new_path, new_image)

	sample['image'] = new_path
	sample['bbox'] = new_bbox
	sample['camera'] = new_cam
	sample['depth_bbox'] = depth_bbox

	return sample


def get_ntu_group(phase, args):

	assert os.path.isdir(args.data_down_path)

	detector = utils.Detector()

	with open(os.path.join(args.data_root_path, 'cameras.pkl'), 'rb') as file:
		color_cameras = pickle.load(file)

	with open(os.path.join(args.data_root_path, 'depth_cameras.pkl'), 'rb') as file:
		depth_cameras = pickle.load(file)

	sample_files = glob.glob(os.path.join(args.data_root_path, 'midway_samples', '*.pkl'))

	sample_files = [file for file in sample_files if by_sequence(phase, file)]

	sample_files.sort()

	for i_cam, sample_file in enumerate(sample_files):

		final_samples = []

		cam_id = os.path.basename(sample_file).split('.')[0]

		print('=> handles camera[', cam_id, ']: [', i_cam, '|', len(sample_files), ']')

		cameras = (color_cameras[cam_id], depth_cameras[cam_id])

		with open(sample_file, 'rb') as file:
			samples_cur_cam = pickle.load(file)

		samples_by_video = utils.groupby(samples_cur_cam, lambda sample: sample['video'])

		for i_vid, (video_id, samples_cur_video) in enumerate(samples_by_video.items()):

			print('\t => handles video[', video_id, ']: [', i_vid, '|', len(samples_by_video), ']')

			samples_by_frame = utils.groupby(samples_cur_video, lambda sample: sample['frame'])

			video_path = os.path.join(args.data_root_path, 'nturgb+d_rgb', video_id + '_rgb.avi')

			down_path = os.path.join(args.data_down_path, video_id)

			if not os.path.exists(down_path):
				os.mkdir(down_path)

			args.down_path = down_path

			for frame, image in enumerate(utils.prefetch(video_path)):

				if frame in samples_by_frame:
					print('\t\t => handles frame[', frame, ']')

					samples_cur_frame = samples_by_frame[frame]

					det_bboxes = detector.detect(image)

					iou_matrix = np.array([[boxlib.iou(sample['bbox'], bbox) for bbox in det_bboxes] for sample in samples_cur_frame])

					sample_indices, det_indices = scipy.optimize.linear_sum_assignment(-iou_matrix)

					for i_sample, i_det in zip(sample_indices, det_indices):

						cur_sample = samples_cur_frame[i_sample]

						if (0.5 <= iou_matrix[i_sample, i_det]):

							cur_sample['bbox'] = det_bboxes[i_det]

							final_samples.append(make_sample(cur_sample, cameras, image, args))

		with open(sample_file.replace('midway', 'final'), 'wb') as file:
			pickle.dump(final_samples, file)


def get_pku_group(args):
	
	assert os.path.isdir(args.data_down_path)

	with open(os.path.join(args.data_root_path, 'cameras.pkl'), 'rb') as file:
		cameras = pickle.load(file)

	detector = utils.Detector()

	sample_file = os.path.join(args.data_root_path, 'midway_samples.pkl')

	with open(sample_file, 'rb') as file:
		samples = pickle.load(file)

	samples_by_video = utils.groupby(samples, lambda sample: sample['video'])

	final_samples = []

	exclusions = json.load(open(os.path.join(args.data_root_path, 'exclusions.json')))

	for video_id in exclusions:
		if video_id in samples_by_video:
			del samples_by_video[video_id]

	for i_vid, (video_id, samples_cur_video) in enumerate(samples_by_video.items()):

		print('=> handles video[', video_id, ']: [', i_vid, '|', len(samples_by_video), ']')

		samples_by_frame = utils.groupby(samples_cur_video, lambda sample: sample['frame'])

		video_path = os.path.join(args.data_root_path, 'RGB_VIDEO', video_id + '.avi')
		depth_path = os.path.join(args.data_root_path, 'DEPTH_VIDEO', video_id + '-depth.avi')

		video_loader = utils.prefetch(video_path, True)
		depth_loader = utils.depth_prefetch(depth_path, True)

		down_path = os.path.join(args.data_down_path, video_id)

		if not os.path.exists(down_path):
			os.mkdir(down_path)

		args.down_path = down_path

		cur_cams = (cameras['color'], cameras[video_id[-1]])

		for frame, (image, depth_image) in enumerate(zip(video_loader, depth_loader)):

			if frame not in samples_by_frame:
				continue

			print('\t=> handles frame[', frame, ']')

			new_depth_path = os.path.join(args.data_root_path, 'DEPTH_IMAGE', video_id + '.' + str(frame) + '.png')

			flag = False

			samples_cur_frame = samples_by_frame[frame]

			det_bboxes = detector.detect(image)

			iou_matrix = np.array([[boxlib.iou(sample['bbox'], bbox) for bbox in det_bboxes] for sample in samples_cur_frame])

			sample_indices, det_indices = scipy.optimize.linear_sum_assignment(-iou_matrix)

			for i_sample, i_det in zip(sample_indices, det_indices):

				cur_sample = samples_cur_frame[i_sample]

				if (0.5 <= iou_matrix[i_sample, i_det]):

					cur_sample['bbox'] = det_bboxes[i_det]

					final_samples.append(make_sample(cur_sample, cur_cams, image, args))

					flag = True

			if flag and not os.path.exists(new_depth_path):
				cv2.imwrite(new_depth_path, depth_image)

	with open(sample_file.replace('midway', 'final'), 'wb') as file:
		pickle.dump(final_samples, file)
