#!/usr/bin/env python3.8
import copy
import math

import image_geometry
import numpy as np
from numpy.linalg import inv
import rospy
# VERY IMPORTANT TO SUBSCRIBE TO MULTIPLE TOPICS
import message_filters
import tf2_ros
from geometry_msgs.msg import Twist, PoseStamped
from sensor_msgs.msg import *
from cv_bridge import CvBridge
from visualization_msgs.msg import *
import cv2
import tf2_geometry_msgs # Do not use geometry_msgs. Use this for PoseStamped (depois perguntar porque)


class Driver:

    def __init__(self):
        # name of the car with \ and without
        self.node = rospy.get_name()
        self.name = self.node[1:len(self.node)]
        # Define the goal to which the robot should move
        self.goal = PoseStamped
        self.goal_active = False
        # get param to see the photos
        self.image_flag = rospy.get_param('~image_flag', 'True')
        # pos initialization -------------------
        self.preyPos = PoseStamped
        self.attackerPos = PoseStamped
        self.teammatePos = PoseStamped
        # ----------------------------
        # colors inicialization ----------------
        self.attacker_color_min = (0, 0, 239)
        self.attacker_color_max = (31, 31, 255)
        self.prey_color_min = (0, 239, 0)
        self.prey_color_max = (31, 255, 31)
        self.teammate_color_min = (236, 0, 0)
        self.teammate_color_max = (255, 31, 31)
        # ---------------------------------
        self.tf_buffer = tf2_ros.Buffer()
        self.listener = tf2_ros.TransformListener(self.tf_buffer)
        # publishes the marker of the cars
        self.publish_marker = rospy.Publisher(self.node + '/Markers', Marker, queue_size=1)
        # publishes the velocity of the car
        self.publisher_goal = rospy.Publisher(str(self.node) + '/cmd_vel', Twist, queue_size=1)
        # sees the goal 0.1s at a time
        self.timer = rospy.Timer(rospy.Duration(0.1), self.sendCommandCallback)
        # subscribes to see if theres a goal ( this part is going to be changed to the value )
        # stops existing, we need a if or a switch to choose the mode (attack, defense, navigating
        self.goal_subscriber = rospy.Subscriber('/move_base_simple/goal', PoseStamped, self.goalReceivedCallBack)
        # sees the team of the car
        self.whichTeam()
        self.br = CvBridge()
        # initialization of the list of laser scan points
        self.points = []
        self.wp_to_pixels = []
        # subscribe to the laser scan values
        self.laser_subscriber = rospy.Subscriber(self.node + '/scan', LaserScan, self.Laser_Points)
        # Get the camera info, in this case is better in static values since the cameras have all the same values
        self.cameraIntrinsic = np.array([[1060.9338813153618, 0.0, 640.5],
                                        [0.0, 1060.9338813153618, 360.5],
                                        [0.0, 0.0, 1.0]])

        # camera extrinsic from the lidar to the camera (back and front are diferent extrinsic values )
        # this is the value from camera_rgb_optical_frame to scan
        self.lidar2cam = np.array([[0.0006, -1.0, -0.0008, -0.0],
                                  [0.0006, 0.0008, -1.0, -0.029],
                                  [1.0, 0.0006, 0.0006, -0.140]])

        self.lidar2cam_back = np.array([[-0.0027, 1.0, -0.0002, 0.022],
                                        [-0.0009, -0.0002, -1.0, -0.029],
                                        [-1.0, -0.0027, 0.0009, -0.139]])

        # subscribes to the back and front images of the car
        self.image_subscriber_front = message_filters.Subscriber(self.node + '/camera/rgb/image_raw', Image)
        self.image_subscriber_back = message_filters.Subscriber(self.node + '/camera_back/rgb/image_raw', Image)
        ts = message_filters.TimeSynchronizer([self.image_subscriber_front, self.image_subscriber_back], 1)
        ts.registerCallback(self.GetImage)


    def whichTeam(self):
        red_names = rospy.get_param('/red_players')
        green_names = rospy.get_param('/green_players')
        blue_names = rospy.get_param('/blue_players')
        for idx, x in enumerate(red_names):
            if self.name == x:
                print('I am ' + str(self.name) + ' I am team red. I am hunting' + str(green_names) + 'and fleeing from' + str(blue_names))
                self.attacker_color_min = (120, 0, 0)
                self.attacker_color_max = (255, 31, 31)
                self.prey_color_min = (0, 100, 0)
                self.prey_color_max = (31, 255, 31)
                self.teammate_color_min = (0, 0, 100)
                self.teammate_color_max = (31, 31, 255)

            elif self.name == green_names[idx]:
                print('I am ' + str(self.name) + ' I am team green. I am hunting' + str(blue_names) + 'and fleeing from' + str(red_names))
                self.prey_color_min = (120, 0, 0)
                self.prey_color_max = (255, 31, 31)
                self.teammate_color_min = (0, 100, 0)
                self.teammate_color_max = (31, 255, 31)
                self.attacker_color_min = (0, 0, 100)
                self.attacker_color_max = (31, 31, 255)

            elif self.name == blue_names[idx]:
                print('I am ' + str(self.name) + ' I am team blue. I am hunting' + str(red_names) + 'and fleeing from' + str(green_names))
                self.teammate_color_min = (120, 0, 0)
                self.teammate_color_max = (255, 31, 31)
                self.attacker_color_min = (0, 100, 0)
                self.attacker_color_max = (31, 255, 31)
                self.prey_color_min = (0, 0, 100)
                self.prey_color_max = (31, 31, 255)
            else:
                pass

    def goalReceivedCallBack(self, goal_msg):

        self.goal = goal_msg # storing the goal inside the class
        self.goal_active = True

    def sendCommandCallback(self, msg):

        # Decision outputs a speed (linear velocity) and an angle (angular velocity)
        # input : goal
        # output : angle and speed

        # verify if the goal is achieved
        if self.goal_active:
            distance_to_goal = self.computeDistanceToGoal(self.goal)
            print(distance_to_goal)
            if distance_to_goal < 0.05:
                rospy.logwarn('I have achieved my goal!!!')
                self.goal_active = False

        # define driving behaviour according to the goal
        if self.goal_active:
            angle, speed = self.driveStraight(self.goal)
            if angle is None or speed is None: # can't transform target frame
                angle = 0
                speed = 0
        else:
            angle = 0
            speed = 0

        # Build the command message (Twist) and publish it
        command_msg = Twist()
        command_msg.linear.x = speed
        command_msg.angular.z = angle
        self.publisher_goal.publish(command_msg)

    def computeDistanceToGoal(self, goal):

        goal_present_time = copy.deepcopy(goal)
        goal_present_time.header.stamp = rospy.Time.now()

        target_frame = self.name + '/base_link'
        try:
            goal_in_base_link = self.tf_buffer.transform(goal_present_time, target_frame, rospy.Duration(1))
        except (tf2_ros.LookupException, tf2_ros.ConnectivityException, tf2_ros.ExtrapolationException):
            rospy.logerr(
                'Could not transform goal from ' + goal.header.frame_id + ' to ' + target_frame + '. Will ignore this goal')
            return None, None

        x = goal_in_base_link.pose.position.x
        y = goal_in_base_link.pose.position.y

        distance = math.sqrt(x**2 + y**2)
        return distance

    def driveStraight(self, goal, minimum_speed=0.1, maximum_speed=1.0):
        """
        :param goal: where the robot wants to go
        :param minimum_speed: min speed the robot can go
        :param maximum_speed: max speed the robot can go
        :return: the angle and speed to use as command
        """
        goal_present_time = copy.deepcopy(goal)
        goal_present_time.header.stamp = rospy.Time.now()

        target_frame = self.name + '/base_link'
        try:
            goal_in_base_link = self.tf_buffer.transform(goal_present_time, target_frame, rospy.Duration(1))

        except (tf2_ros.LookupException, tf2_ros.ConnectivityException, tf2_ros.ExtrapolationException):
            rospy.logerr('Could not transform goal from ' + goal.header.frame_id + ' to ' + target_frame + '. Will ignore')
            return None, None

        x = goal_in_base_link.pose.position.x
        y = goal_in_base_link.pose.position.y

        angle = math.atan2(y, x) # compute the angle

        distance = math.sqrt(x**2 + y**2)
        speed = 0.5 * distance
        # saturates the speed to minimum and maximum values
        speed = min(speed, maximum_speed)
        speed = max(speed, minimum_speed)

        return angle, speed

    def GetImage(self, data_front, data_back):
        # rospy.loginfo('Image received...')
        image_front = self.br.imgmsg_to_cv2(data_front, "bgr8")
        image_back = self.br.imgmsg_to_cv2(data_back, "bgr8")

        # Convert the image to a Numpy array since most cv2 functions

        # require Numpy arrays.
        frame_front = np.array(image_front, dtype=np.uint8)
        frame_back = np.array(image_back, dtype=np.uint8)

        # Process the frame using the process_image() function
        display_image_front = self.discover_car(frame_front, self.lidar2cam)
        display_image_back = self.discover_car(frame_back, self.lidar2cam_back)
        # with this we can see the red, blue and green cars
        if self.image_flag is True:
            cv2.imshow('front', display_image_front)
            cv2.imshow('back', display_image_back)
        cv2.waitKey(1)

    def discover_car(self, frame, camera_matrix):
        # Convert to HSV
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # create 3 channels !
        gray = cv2.merge((gray, gray, gray))
        mask_attacker = cv2.inRange(frame, self.attacker_color_min, self.attacker_color_max)
        mask_prey = cv2.inRange(frame, self.prey_color_min, self.prey_color_max)
        mask_teammate = cv2.inRange(frame, self.teammate_color_min, self.teammate_color_max)
        # get the transform the lidar values to pixel
        pixel_cloud = self.lidar_to_image(camera_matrix)
        # creates the image
        mask_final = mask_attacker + mask_prey + mask_teammate
        image = cv2.bitwise_or(frame, frame, mask=mask_final)
        image = cv2.add(gray, image)
        # gives the center of a mask and draws it
        Center_t = self.GetCentroid(mask_teammate, image)
        Center_p = self.GetCentroid(mask_prey, image)
        Center_a = self.GetCentroid(mask_attacker, image)
        self.wp_to_pixels = []
        # draws the lidar points in the image
        for value in pixel_cloud:
            if math.isnan(value[0]) is False:
                world_pixels = [value[0]/value[2], value[1]/value[2], value[2]]
                image = cv2.circle(image, (int(world_pixels[0]), int(world_pixels[1])), radius=0, color=(0, 200, 125), thickness=6)
                self.wp_to_pixels.append(world_pixels)
                # with this we have the pixel points of the lidar, now we need to use this list, check the closest point
                # from the centroid (depending which centroid is, or if there is one)
            else:
                self.wp_to_pixels.append([-10, -10, -10])
        # probably here it receives the self.attackerPos , self.preyPos and self.teammatePos in case they exist
        self.preyPos = self.ClosestPoint(Center_p, camera_matrix)
        self.attackerPos = self.ClosestPoint(Center_a, camera_matrix)
        self.teammatePos = self.ClosestPoint(Center_t, camera_matrix)
        # print(self.preyPos)
        # print(self.attackerPos)
        # print(self.teammatePos)
        return image

    def ClosestPoint(self, Center, camera_matrix):
        Close_lidar_point = PoseStamped()
        dist = [0]
        if Center[0] is None:
            Close_lidar_point.pose.position.x = -1000
            Close_lidar_point.pose.position.y = -1000
            return Close_lidar_point
        else:
            for idx, pixel in enumerate(self.wp_to_pixels):
                dist.append(math.sqrt((Center[0]-pixel[0])**2 + (Center[1]-pixel[1])**2))
                if dist[idx + 1] > dist[idx]:
                    Close_lidar_point.pose.position.x = self.points[idx][0]
                    Close_lidar_point.pose.position.y = self.points[idx][1]

            return Close_lidar_point

    def sendMarker(self, coord):
        marker = Marker()
        marker.id = 0
        marker.header.frame_id = "red1/base_link"
        marker.type = marker.CYLINDER
        marker.action = marker.ADD
        marker.scale.x = 0.2
        marker.scale.y = 0.2
        marker.scale.z = 0.2
        marker.color.r = 1
        marker.color.g = 0
        marker.color.b = 0
        marker.color.a = 1.0
        marker.pose.orientation.w = 1.0
        marker.pose.position.x = coord[0] + 0.2
        marker.pose.position.y = coord[1] + 0.011
        marker.pose.position.z = 0.2
        self.publish_marker.publish(marker)

    def lidar_to_image(self, camera_matrix):
        """
        :param camera_matrix: attacker points from the camera
        :return:
        """
        # so testar os valores front por agora
        pixel_cloud =[]
        for value in self.points:
            value_array = np.array(value)
            pixel = np.dot(camera_matrix, value_array.transpose())
            pixel = np.dot(self.cameraIntrinsic, pixel)
            pixel_cloud.append(pixel)

        return pixel_cloud

    def Laser_Points(self, msg):
        """
        :param msg: scan data received from the car
        :return:
        """
        # creates a list of world coordinates
        z = 0
        for idx, range in enumerate(msg.ranges):
            theta = msg.angle_min + idx * msg.angle_increment
            x = range * math.cos(theta)
            y = range * math.sin(theta)
            self.points.append([x, y, z, 1])

    def GetCentroid(self, mask, image):
        M = cv2.moments(mask)
        if M["m00"] != 0.0:
            cX = int(M["m10"] / M["m00"])
            cY = int(M["m01"] / M["m00"])
            cv2.circle(image, (cX, cY), 7, (255, 255, 255), -1)
            return cX, cY
        else:
            # sends an impossible value that doesnt
            return None, None


def main():
    rospy.init_node('p_spombinho_driver', anonymous=False)
    driver = Driver()
    rospy.spin()


if __name__ == '__main__':
  main()