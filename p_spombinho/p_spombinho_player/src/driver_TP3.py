#!/usr/bin/env python3.8
import copy
import math

import numpy as np
import rospy
# VERY IMPORTANT TO SUBSCRIBE TO MULTIPLE TOPICS
import message_filters
import tf2_ros
from geometry_msgs.msg import Twist, PoseStamped
from sensor_msgs.msg import *
from cv_bridge import CvBridge
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
        # publishes the velocity of the car
        self.publisher_command = rospy.Publisher(str(self.node) + '/cmd_vel', Twist, queue_size=1)
        # sees the goal 0.1s at a time
        self.timer = rospy.Timer(rospy.Duration(0.1), self.sendCommandCallback)
        # subscribes to see if theres a goal ( this part is going to be changed to the value )
        self.goal_subscriber = rospy.Subscriber('/move_base_simple/goal', PoseStamped, self.goalReceivedCallBack)
        # sees the team of the car
        self.whichTeam()
        self.br = CvBridge()
        # initialization of the list of laser scan points
        self.points = []
        # subscribe to the laser scan values
        self.laser_subscriber = rospy.Subscriber(self.node + '/scan', LaserScan, self.Laser_Points)
        # Get the camera info, in this case is better in static values since the cameras have all the same values
        self.cameraIntrinsic = [1060.9338813153618, 0.0, 640.5, -74.26537169207533,
                                0.0, 1060.9338813153618, 360.5, 0.0,
                                0.0, 0.0, 1.0, 0.0]
        # camera extrinsic from the lidar to the camera (back and front are diferent extrinsic values )
        self.cameraExtrinsicFront = [1.0, 0.0, 0.0, -0.073,
                                     0.0, 1.0, 0.0, 0.011,
                                     0.0, 0.0, 1.0, -0.084,
                                     0.0, 0.0, 0.0, 1.0]

        self.cameraExtrinsicBack = [-1.0, 0.0, 0.0, 0.2,
                                     0.0, -1.0, 0.0, 0.011,
                                     0.0, 0.0, 1.0, -0.084,
                                     0.0, 0.0, 0.0, 1.0]

        # subscribes to the back and front images of the car
        self.image_subscriber_front = message_filters.Subscriber(self.node + '/camera/rgb/image_raw', Image)
        self.image_subscriber_back = message_filters.Subscriber(self.node + '/camera_back/rgb/image_raw', Image)
        ts = message_filters.TimeSynchronizer([self.image_subscriber_front, self.image_subscriber_back], 1)
        ts.registerCallback(self.GetImagePrey)


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
        self.publisher_command.publish(command_msg)

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

    def GetImagePrey(self, data_front, data_back):
        rospy.loginfo('Image received...')
        image_front = self.br.imgmsg_to_cv2(data_front, "bgr8")
        image_back = self.br.imgmsg_to_cv2(data_back, "bgr8")

        # Convert the image to a Numpy array since most cv2 functions

        # require Numpy arrays.
        frame_front = np.array(image_front, dtype=np.uint8)
        frame_back = np.array(image_back, dtype=np.uint8)

        # Process the frame using the process_image() function
        display_image_front = self.discover_car(frame_front)
        display_image_back = self.discover_car(frame_back)
        # with this we can see the red, blue and green cars
        #TODO: CREATE HERE A ARGUMENT TO SEE OR NOT THE IMAGES
        cv2.imshow('front', display_image_front)
        cv2.imshow('back', display_image_back)
        cv2.waitKey(1)

    def discover_car(self, frame):
        # Convert to HSV
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # create 3 channels !
        gray = cv2.merge((gray, gray, gray))
        mask_attacker = cv2.inRange(frame, self.attacker_color_min, self.attacker_color_max)
        mask_prey = cv2.inRange(frame, self.prey_color_min, self.prey_color_max)
        mask_teammate = cv2.inRange(frame, self.teammate_color_min, self.teammate_color_max)
        self.sensor_fusion(mask_attacker, mask_prey, mask_teammate, self.points)
        mask_final = mask_attacker + mask_prey + mask_teammate
        image = cv2.bitwise_or(frame, frame, mask=mask_final)
        image = cv2.add(gray, image)

        return image

    def sensor_fusion(self, attacker, prey, teammate, lidar_points):
        '''
        :param attacker: attacker points from the camera
        :param prey: prey points from the camera
        :param teammate: teammate points from the camera
        :param lidar_points: points received by the lidar
        :return:
        '''

    def Laser_Points(self, msg):
        '''
        :param msg: scan data received from the car
        :return:
        '''
        # creates a list of world coordinates
        z = 0
        for idx, range in enumerate(msg.ranges):
            theta = msg.angle_min + idx * msg.angle_increment
            x = range * math.cos(theta)
            y = range * math.sin(theta)
            self.points.append([x, y, z])


def main():
    rospy.init_node('p_spombinho_driver', anonymous=False)
    driver = Driver()
    rospy.spin()


if __name__ == '__main__':
  main()