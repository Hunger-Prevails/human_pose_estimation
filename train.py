import torch
import torch.nn as nn
import numpy as np
import torch.optim as optim
import utils

from torch.autograd import Variable

class Trainer:

    def __init__(self, args, model, joint_info):
        self.model = model
        self.joint_info = joint_info

        self.nGPU = args.nGPU
        self.depth = args.depth
        self.num_joints = args.num_joints
        self.side_eingabe = args.side_eingabe
        self.side_ausgabe = args.side_ausgabe
        self.depth_range = args.depth_range
        self.flip_test = args.flip_test
        self.semi_cubic = args.semi_cubic
        self.unimodal = args.unimodal

        self.learn_rate = args.learn_rate
        self.num_epochs = args.n_epochs
        self.grad_norm = args.grad_norm

        self.thresholds = dict(
                            score = args.score_thresh,
                            perfect = args.perfect_thresh,
                            good = args.good_thresh,
                            jitter = args.jitter_thresh)

        self.optimizer = optim.Adam(model.parameters(), args.learn_rate, weight_decay = args.weight_decay)

        self.criterion = nn.__dict__[args.criterion + 'Loss'](reduction = 'mean')
        if args.nGPU > 0:
            self.criterion = self.criterion.cuda()


    def train(self, epoch, train_loader):
        n_batches = len(train_loader)

        loss_avg = 0
        total = 0
        
        self.model.train()
        self.learning_rate(epoch)

        cuda_device = torch.device('cuda')

        for i, (image, true_coords, intrinsics, valid_mask) in enumerate(train_loader):
            
            if self.nGPU > 0:
                image = image.to(cuda_device)

                true_coords = true_coords.to(cuda_device)

                intrinsics = intrinsics.to(cuda_device)

                valid_mask = valid_mask.unsqueeze(-1).to(cuda_device)
                
            batch_size = image.size(0)
            
            ausgabe = self.model(image)

            heatmap = utils.to_heatmap(ausgabe, self.depth, self.num_joints, self.side_ausgabe, self.side_ausgabe)

            if self.flip_test:
                ausgabe_flip = self.model(image[:, :, :, ::-1])

                heatmap_flip = utils.to_heatmap(ausgabe, self.depth, self.num_joints, self.side_ausgabe, self.side_ausgabe)
                heatmap_flip = heatmap_flip[:, self.joint_info.mirror, :, ::-1]

                heatmap = 0.5 * (heatmap + heatmap_flip)

            key_index = self.joint_info.key_index

            if self.semi_cubic:
                key_depth = true_coords[:, key_index:key_index + 1, 2]

                prediction = utils.to_coordinate(heatmap, self.side_eingabe, self.depth_range, intrinsics, key_depth)
            else:
                prediction = utils.decode(heatmap, self.depth_range)

                relative = prediction - prediction[:, key_index:key_index + 1]

                true_relative = true_coords - true_coords[:, key_index:key_index + 1]

            loss = self.criterion(relative * valid_mask, true_relative * valid_mask)

            self.optimizer.zero_grad()
            loss.backward()

            nn.utils.clip_grad_norm_(self.model.parameters(), self.grad_norm)
            self.optimizer.step()

            loss_avg += loss.item() * batch_size
            total += batch_size
            
            print "| train Epoch[%d] [%d/%d]  Loss %1.4f" % (epoch, i, n_batches, loss.item())

        loss_avg /= total

        print "\n=> train Epoch[%d]  Loss: %1.4f\n" % (epoch, loss_avg)

        return dict(train_loss = loss_avg)
        

    def test(self, epoch, test_loader):
        n_batches = len(test_loader)

        loss_avg = 0
        total = 0

        self.model.eval()
        self.learning_rate(epoch)

        cuda_device = torch.device('cuda')

        scores_and_stats = []

        for i, (image, true_coords, intrinsics, back_rotation, valid_mask) in enumerate(test_loader):
            
            if self.nGPU > 0:
                image = image.to(cuda_device)

                true_coords = true_coords.to(cuda_device)

                intrinsics = intrinsics.to(cuda_device)

                mask_valid = valid_mask.unsqueeze(-1).to(cuda_device)

            batch_size = image.size(0)

            with torch.no_grad():                
                ausgabe = self.model(image)

                heatmap = utils.to_heatmap(ausgabe, self.depth, self.num_joints, self.side_ausgabe, self.side_ausgabe, self.unimodal)

                if self.flip_test:
                    ausgabe_flip = self.model(image[:, :, :, ::-1])

                    heatmap_flip = utils.to_heatmap(ausgabe, self.depth, self.num_joints, self.side_ausgabe, self.side_ausgabe, self.unimodal)
                    heatmap_flip = heatmap_flip[:, self.joint_info.mirror, :, ::-1]

                    heatmap = 0.5 * (heatmap + heatmap_flip)

                key_index = self.joint_info.key_index

                if self.semi_cubic:
                    key_depth = true_coords[:, key_index:key_index + 1, 2]

                    prediction = utils.to_coordinate(heatmap, self.side_eingabe, self.depth_range, intrinsics, key_depth)

                    loss = self.criterion(prediction * mask_valid, true_coords * mask_valid)
                else:
                    prediction = utils.decode(heatmap, self.depth_range)

                    relative = prediction - prediction[:, key_index:key_index + 1]
                    
                    true_relative = true_coords - true_coords[:, key_index:key_index + 1]

                    loss = self.criterion(relative * mask_valid, true_relative * mask_valid)

                loss_avg += loss.item() * batch_size
                total += batch_size

            if self.semi_cubic:
                prediction = prediction.cpu().numpy()
                true_coords = true_coords.cpu().numpy()
        
                prediction += true_coords[:, key_index:key_index + 1] - prediction[:, key_index:key_index + 1]
            else:
                relative = relative.cpu().numpy()
                true_coords = true_coords.cpu().numpy()
            
                prediction = relative + true_coords[:, key_index:key_index + 1]

            prediction = np.einsum('Bij,BCj->BCi', back_rotation, prediction)
            true_coords = np.einsum('Bij,BCj->BCi', back_rotation, true_coords)

            scores_and_stats.append(self.analyze(prediction, true_coords, valid_mask, self.joint_info.mirror, key_index))

            print "| test Epoch[%d] [%d/%d]  Loss %1.4f" % (epoch, i, n_batches, loss.item())

        loss_avg /= total

        summary = dict(test_loss = loss_avg)
        summary.update(utils.parse_epoch(scores_and_stats, total))

        print '\n=> test Epoch[%d]  Loss: %1.4f  overall_mean: %1.3f' % (epoch, loss_avg, summary['overall_mean'])
        print 'pck: %1.3f  auc: %1.3f\n' % (summary['score_pck'], summary['score_auc'])

        return summary


    def analyze(self, prediction, true_coords, valid_mask, mirror, key_index):
        '''
        Analyzes prediction against true_coords under original camera

        Args:
            prediction: (batch_size, num_joints, 3)
            true_coords: (batch_size, num_joints, 3)
            valid_mask: (batch_size, num_joints)
            mirror: (num_joints,)

        Returns:
            batch_size, scores and statistics

        '''
        prediction -= prediction[:, key_index:key_index + 1]
        true_coords -= true_coords[:, key_index:key_index + 1]

        cubics = np.linalg.norm(prediction - true_coords, axis = -1)
        reflects = np.linalg.norm(prediction - true_coords[:, mirror], axis = -1)
        tangents = np.linalg.norm(prediction[:, :, :2] - true_coords[:, :, :2], axis = -1)

        valid = np.where(valid_mask.flatten() == 1.0)[0]

        cubics = cubics.flatten()[valid]
        reflects = reflects.flatten()[valid]
        tangents = tangents.flatten()[valid]

        overall_mean = np.mean(cubics)
        score_pck = np.mean(cubics / self.thresholds['score'] <= 1.0)
        score_auc = np.mean(np.maximum(0, 1 - cubics / self.thresholds['score']))

        stats = utils.statistics(cubics, reflects, tangents, self.thresholds)

        stats.update(dict(
                        batch_size = prediction.shape[0],
                        score_pck = score_pck,
                        score_auc = score_auc,
                        overall_mean = overall_mean))

        return stats


    def learning_rate(self, epoch):
        if epoch - 1 < self.num_epochs * 0.6:
            learn_rate = self.learn_rate
        elif epoch - 1 < self.num_epochs * 0.9:
            learn_rate = self.learn_rate * 0.2
        else:
            learn_rate = self.learn_rate * 0.04

        for group in self.optimizer.param_groups:
            group['lr'] = learn_rate
