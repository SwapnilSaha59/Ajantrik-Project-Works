import cv2
import torch
import numpy as np
from copy import deepcopy
from PINet.hourglass_network import lane_detection_network
from torch.autograd import Variable

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

class LaneDetection():

    def __init__(self, model_path, model_name):
        self.lane_agent = lane_detection_network()
        self.lane_agent.load_state_dict(
            torch.load(model_path, map_location=device),False)
        self.lane_agent = self.lane_agent.to(device)
        # self.lane_agent.eval()

        if model_name == 'culane':
            self.threshold_point = 0.96 #0.88 #0.93 #0.95 #0.93
        if model_name == 'curvelane':
            self.threshold_point = 0.81 #0.35 #0.5 #0.57 #0.64 #0.35

        self.threshold_instance = 0.08
        self.x_size = 512
        self.y_size = 256
        self.resize_ratio = 8
        self.grid_x = self.x_size//self.resize_ratio  #64
        self.grid_y = self.y_size//self.resize_ratio  #32


    def Testing(self, frame, mask):
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            
        frame = cv2.resize(frame, (512,256))/255.0
        mask = cv2.resize(mask, (512,256))

        frame = np.rollaxis(frame, axis=2, start=0)
        _, _, ti = self.test(self.lane_agent, np.array([frame]), mask, self.threshold_point) 
        ti[0] = cv2.resize(ti[0], (1280,720))
    
        return ti[0]
    

    def predict_lanes_test(self, inputs):
        inputs = torch.from_numpy(inputs).float() 
        inputs = Variable(inputs).to(device)
        outputs, features = self.lane_agent(inputs)

        return outputs


    def test(self, lane_agent, test_images, mask, thresh, index= -1):

        result = self.predict_lanes_test(test_images)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        confidences, offsets, instances = result[index]
        
        num_batch = len(test_images)

        out_x = []
        out_y = []
        out_images = []
        
        for i in range(num_batch):
            image = deepcopy(test_images[i])
            image =  np.rollaxis(image, axis=2, start=0)
            image =  np.rollaxis(image, axis=2, start=0)*255.0
            image = image.astype(np.uint8).copy()

            confidence = confidences[i].view(self.grid_y, self.grid_x).cpu().data.numpy()

            offset = offsets[i].cpu().data.numpy()
            offset = np.rollaxis(offset, axis=2, start=0)
            offset = np.rollaxis(offset, axis=2, start=0)
            
            instance = instances[i].cpu().data.numpy()
            instance = np.rollaxis(instance, axis=2, start=0)
            instance = np.rollaxis(instance, axis=2, start=0)

            # generate point and cluster
            raw_x, raw_y = self.generate_result(confidence, offset, instance, thresh)

            # eliminate fewer points
            in_x, in_y = self.eliminate_fewer_points(raw_x, raw_y)
                    
            # sort points along y 
            in_x, in_y = self.sort_along_y(in_x, in_y)  

            # passing mask for extracting Roi results
            result_image = self.draw_points(in_x, in_y, deepcopy(image), mask)

            out_x.append(in_x)
            out_y.append(in_y)
            out_images.append(result_image)

        return out_x, out_y,  out_images

    ############################################################################
    ## eliminate result that has fewer points than threshold
    ############################################################################
    def eliminate_fewer_points(self, x, y):
        # eliminate fewer points
        out_x = []
        out_y = []
        for i, j in zip(x, y):
            if len(i)>5:
                out_x.append(i)
                out_y.append(j)     
        return out_x, out_y   

    ############################################################################
    ## generate raw output
    ############################################################################
    def generate_result(self, confidance, offsets,instance, thresh):

        mask = confidance > thresh
        grid_location = np.zeros((self.grid_y, self.grid_x, 2))
        for y in range(self.grid_y):
            for x in range(self.grid_x):
                grid_location[y][x][0] = x
                grid_location[y][x][1] = y

        grid = grid_location[mask]
        offset = offsets[mask]
        feature = instance[mask]

        lane_feature = []
        x = []
        y = []
        for i in range(len(grid)):
            if (np.sum(feature[i]**2))>=0:
                point_x = int((offset[i][0]+grid[i][0])*self.resize_ratio)
                point_y = int((offset[i][1]+grid[i][1])*self.resize_ratio)
                if point_x > self.x_size or point_x < 0 or point_y > self.y_size or point_y < 0:
                    continue
                if len(lane_feature) == 0:
                    lane_feature.append(feature[i])
                    x.append([point_x])
                    y.append([point_y])
                else:
                    flag = 0
                    index = 0
                    min_feature_index = -1
                    min_feature_dis = 10000
                    for feature_idx, j in enumerate(lane_feature):
                        dis = np.linalg.norm((feature[i] - j)**2)
                        if min_feature_dis > dis:
                            min_feature_dis = dis
                            min_feature_index = feature_idx
                    if min_feature_dis <= self.threshold_instance:
                        lane_feature[min_feature_index] = (lane_feature[min_feature_index]*len(x[min_feature_index]) + feature[i])/(len(x[min_feature_index])+1)
                        x[min_feature_index].append(point_x)
                        y[min_feature_index].append(point_y)
                    elif len(lane_feature) < 12:
                        lane_feature.append(feature[i])
                        x.append([point_x])
                        y.append([point_y])
                    
        return x, y

    ############################################################################
    ## sort points along y 
    ############################################################################
    def sort_along_y(self, x, y):
        out_x = []
        out_y = []

        for i, j in zip(x, y):
            i = np.array(i)
            j = np.array(j)

            ind = np.argsort(j, axis=0)
            out_x.append(np.take_along_axis(i, ind[::-1], axis=0).tolist())
            out_y.append(np.take_along_axis(j, ind[::-1], axis=0).tolist())
        
        return out_x, out_y
    
    ############################################################################
    ## draw points 
    ############################################################################
    def draw_points(self, x, y, image, mask):
        color_index = 0
        color = [(0,0,0), (255,0,0), (0,255,0),(0,0,255),
                (255,255,0),(255,0,255),(0,255,255),(255,
                255,255),(100,255,0),(100,0,255),(255,100,0),
                (0,100,255),(255,0,100),(0,255,100)]

        for i, j in zip(x, y):
            color_index += 1
            if color_index > 12:
                color_index = 12
            for index in range(len(i)):

                if np.dot(image[int(j[index]), int(i[index])], mask[int(j[index]), int(i[index])]) != 0:
                    image = cv2.circle(image, (int(i[index]), int(j[index])), 2, color[color_index], -1)

        return image
