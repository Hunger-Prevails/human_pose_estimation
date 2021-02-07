import os
import cv2
import copy
import json
import jpeg4py
import numpy as np
import random
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import depth_groups
import cameralib
import torch
import utils
import pickle5 as pickle
import glob
import torch.utils.data as data

from torchvision import datasets
from torchvision import transforms
from depth_groups import by_person
from augment_colour import random_color


def get_data_loader(args, phase):
    data_info = getattr(depth_groups, 'get_' + args.data_name + '_info')()

    dataset = Dataset(data_info, phase, args)

    shuffle = args.shuffle if phase == 'train' else False

    data_loader = data.DataLoader(dataset, args.batch_size, shuffle, num_workers = args.workers, pin_memory = True)

    return data_loader, data_info


class Dataset(data.Dataset):

    def __init__(self, data_info, phase, args):

        self.data_root_path = args.data_root_path

        self.data_info = data_info
        self.samples = self.get_samples(args, phase)

        with open(os.path.join(args.data_root_path, 'depth_cameras.pkl'), 'rb') as file:
            self.depth_cams = pickle.load(file)

        self.at_test = phase != 'train'
        self.side_in = args.side_in

        assert len(data_info.short_names) == args.num_joints

        self.mean = [0.485, 0.456, 0.406]
        self.dev = [0.229, 0.224, 0.225]
        self.nexponent = args.nexponent
        self.colour = args.colour
        self.geometry = args.geometry
        self.random_zoom = args.random_zoom
        self.to_depth = args.to_depth

        self.transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize(mean=self.mean, std=self.dev)])

    def get_samples(self, args, phase):
        sample_files = glob.glob(os.path.join(args.data_root_path, 'final_samples', '*.pkl'))

        samples = []

        for sample_file in sample_files:
            with open(sample_file, 'rb') as file:
                samples += pickle.load(file)

        with open(os.path.join(args.data_root_path, 'split.json')) as file:
            split = json.load(file)

        return [sample for sample in samples if by_person(split, phase, sample)]

    def get_input_image(self, image_path, camera, bbox, do_flip, random_zoom):
        '''
        Turn towards the center of bbox then crop a square-shaped image aligned with the height of the bbox
        Args:
            image_path: path to the image that matches the camera's current state
            camera: current state of the camera
            bbox: bbox of the person that matches the camera's current state
        '''
        center = bbox[:2] + bbox[2:] / 2
        
        width = np.array([bbox[2] / 2, 0])
        height = np.array([0, bbox[3] / 2])

        if bbox[2] < bbox[3]:
            vertical = True
            near_side = np.stack([center - width, center + width])
            far_side = np.stack([center - height, center + height])
        else:
            vertical = False
            far_side = np.stack([center - width, center + width])
            near_side = np.stack([center - height, center + height])

        new_cam = copy.deepcopy(camera)
        new_cam.turn_towards(center)
        new_cam.undistort()
        new_cam.square_pixels()

        far_side = new_cam.world_to_image(camera.image_to_world(far_side))

        far_dist = np.linalg.norm(far_side[0] - far_side[1])

        new_cam.zoom(self.side_in / far_dist)
        new_cam.center_principal_point((self.side_in, self.side_in))

        if (not self.at_test) and self.geometry:
            new_cam.zoom(random_zoom)

        if do_flip:
            new_cam.horizontal_flip()

        image = plt.imread(image_path)
        image = cameralib.reproject_image(image, camera, new_cam, (self.side_in, self.side_in))

        return image, new_cam

    def parse_sample(self, sample):
        depth_cam = self.depth_cams[sample['video'][:8]]

        seq_folder = os.path.join('nturgbd_depth_s' + sample['video'][1:4], 'nturgb+d_depth')

        image_name = 'Depth-' + str(sample['frame'] + 1).zfill(8) + '.png'

        depth_image = os.path.join(self.data_root_path, seq_folder, sample['video'], image_name)

        do_flip = (not self.at_test) and (np.random.rand() < 0.5)

        random_zoom = np.random.uniform(self.random_zoom, self.random_zoom ** (-1))

        color_image, new_color_cam = self.get_input_image(sample['image'], sample['camera'], sample['bbox'], do_flip, random_zoom)
        depth_image, new_depth_cam = self.get_input_image(depth_image, depth_cam, sample['depth_bbox'], do_flip, random_zoom)

        color_image = self.transform(random_color(color_image) if self.colour else color_image.copy())

        depth_image = depth_image.squeeze()

        if self.to_depth:
            depth_image = utils.to_depth(depth_image, depth_cam)

        depth_image = utils.enhance(depth_image, self.nexponent)

        world_coords = sample['skeleton']
        camera_coords = new_color_cam.world_to_camera(world_coords)
        valid = sample['valid']

        if do_flip:
            camera_coords = camera_coords[self.data_info.mirror]
            valid = valid[self.data_info.mirror]

        if self.at_test:
            color_br = sample['camera'].R @ new_color_cam.R.T

            return color_image, depth_image, camera_coords, valid, color_br
        else:
            return color_image, depth_image, camera_coords, valid

    def __getitem__(self, index):
        return self.parse_sample(self.samples[index])

    def __len__(self):
        return len(self.samples)

    def viz(self, args):
        cam_specs = np.load('./batch.npy')

        for index in range(args.batch_size):

            sample = self.samples[index]

            seq_folder = os.path.join('nturgbd_depth_s' + sample['video'][1:4], 'nturgb+d_depth')

            image_name = 'Depth-' + str(sample['frame'] + 1).zfill(8) + '.png'

            depth_image = os.path.join(self.data_root_path, seq_folder, sample['video'], image_name)

            depth_cam = self.depth_cams[sample['video'][:8]]

            visualize(depth_image, depth_cam, sample['skeleton'], cam_specs[index], sample['depth_bbox'])


def show_mat(image_coord, ax, bbox = None):
    '''
    Shows skeleton(mat)

    Args:
        image_coord: (21, 2)
    '''
    from joint_settings import h36m_short_names as short_names
    from joint_settings import h36m_parent as parent

    mapper = dict(zip(short_names, range(len(short_names))))

    body_edges = [mapper[parent[name]] for name in short_names]
    body_edges = np.hstack(
        [
            np.arange(len(body_edges)).reshape(-1, 1),
            np.array(body_edges).reshape(-1, 1)
        ]
    )
    ax.plot(image_coord[:, 0], image_coord[:, 1], '.', color = 'yellow')

    for edge in body_edges:
        ax.plot(image_coord[edge, 0], image_coord[edge, 1], '--', color = 'b')

    if bbox is not None:
        rect = patches.Rectangle((bbox[0], bbox[1]), bbox[2], bbox[3], linewidth = 2, edgecolor = 'r', facecolor = 'none')
        ax.add_patch(rect)


def visualize(image_name, depth_cam, true_cam, spec_cam, depth_bbox):
    plt.figure(figsize = (16, 12))

    image = plt.imread(image_name) * 255.0
    image = (image / 30.0 * 255.0).astype(np.uint8)

    ax = plt.subplot(1, 2, 1)
    ax.imshow(image, cmap = 'gray', vmin = 0, vmax = 255)
    show_mat(depth_cam.camera_to_image(spec_cam), ax, depth_bbox)

    ax = plt.subplot(1, 2, 2)
    ax.imshow(image, cmap = 'gray', vmin = 0, vmax = 255)
    show_mat(depth_cam.camera_to_image(true_cam), ax, depth_bbox)

    plt.show()
