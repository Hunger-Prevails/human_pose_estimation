import os
import cv2
import copy
import jpeg4py
import numpy as np
import data_groups
import torch
import mat_utils
import augmentation
import torch.utils.data as data

from torch.utils.data import DataLoader
from torchvision import datasets
from torchvision import transforms


def get_comp_loader(args, phase):
    data_group = getattr(comp_groups, 'get_' + args.comp_source + '_group')(phase, args)

    dataset = Lecture(data_group, args) if phase == 'train' else Exam(data_group, args)

    return DataLoader(
            dataset,
            batch_size = args.batch_size,
            shuffle = args.shuffle,
            num_workers = args.workers,
            pin_memory = True
        ), data_group.joint_info


class Lecture(data.Dataset):

    def __init__(self, pose_group, args):

        self.joint_info = pose_group.joint_info
        self.samples = pose_group.samples

        self.random_zoom = args.random_zoom
        self.side_in = args.side_in

        self.mean = [0.485, 0.456, 0.406]
        self.dev = [0.229, 0.224, 0.225]

        self.transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize(mean=self.mean, std=self.dev)])

    def parse_sample(sample):
        image_coords = sample.image_coords

        image = jpeg4py.JPEG(sample.image_path).decode()

        border = np.array(image.shape[:2])[::-1]

        if np.random.rand() < 0.5:

            image = image[:, ::-1].copy()

            image_coords[:, 0] = border[0] - image_coords[:, 0]

            image_coords = image_coords[self.joint_info.mirror]

        roi_center = sample.bbox[:2] + sample.bbox[2:] / 2
        
        roi_side = np.amax(sample.bbox[2:])

        if self.do_perturbate:
            roi_center += np.random.uniform(-0.05, 0.05, size = 2) * sample.bbox[2:]

            image, new_coords = mat_utils.rand_rotate(center = roi_center, image = image, points = image_coords[:, :2], max_radian = np.pi / 6)

            image_coords = np.hstack(new_coords, image_coords[:, 2:])

            roi_side *= np.random.uniform(self.random_zoom, self.random_zoom ** (-1))

        roi_side = int(np.round(roi_side))

        roi_begin = (roi_center - roi_side / 2).astype(np.int)
        roi_end = roi_begin + roi_side

        scale_factor = self.side_in / roi_side

        image_coords[:, :2] = (image_coords[:, :2] - roi_begin) * scale_factor

        feed_in = cv2.resize(image[roi_begin[1]:roi_end[1], roi_begin[0]:roi_end[0]], (side_in, side_in))

        return feed_in, image_coords[:, :2], image_coords[:, 2] != 0

    def __getitem__(self, index):
        return self.parse_sample(self.samples[index])

    def __len__(self):
        return len(self.samples)


class Exam(data.Dataset):

    def __init__(self, pose_group, args):

        self.joint_info = pose_group.joint_info
        self.samples = pose_group.samples

        self.mean = [0.485, 0.456, 0.406]
        self.dev = [0.229, 0.224, 0.225]

        self.transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize(mean=self.mean, std=self.dev)])



    def parse_sample(sample):
        image_coords = sample.image_coords

        image = jpeg4py.JPEG(sample.image_path).decode()

        border = np.array(image.shape[:2])[::-1]

        roi_center = sample.bbox[:2] + sample.bbox[2:] / 2
        
        roi_side = int(np.round(np.amax(sample.bbox[2:])))

        roi_begin = (roi_center - roi_side / 2).astype(np.int)
        roi_end = roi_begin + roi_side

        scale_factor = self.side_in / roi_side

        image_coords[:, :2] = (image_coords[:, :2] - roi_begin) * scale_factor

        feed_in = cv2.resize(image[roi_begin[1]:roi_end[1], roi_begin[0]:roi_end[0]], (side_in, side_in))

        return feed_in, image_coords[:, :2], image_coords[:, 2] != 0

    def __getitem__(self, index):
        return self.parse_sample(self.samples[index])

    def __len__(self):
        return len(self.samples)