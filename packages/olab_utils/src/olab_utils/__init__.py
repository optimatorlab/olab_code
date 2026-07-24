'''
A collection of useful classes from the Optimator Lab (OLab)
'''

from importlib.metadata import PackageNotFoundError, version

try:
	__version__ = version("olab-utils")
except PackageNotFoundError:
	__version__ = "0.0.0"

import numpy as np
import cv2
import os
import math
import socket, errno, time  # For checkPort() function
import warnings  # For deprecated-alias warnings (see findTagPose() etc., issue #21)

import qrcode
import qrcode.constants
from PIL import Image, ImageColor

# https://mavlink.io/en/messages/common.html#MAV_SEVERITY
SEVERITY_EMERGENCY = 0  # System is unusable. This is a "panic" condition.
SEVERITY_ALERT     = 1  # Action should be taken immediately. Indicates error in non-critical systems.
SEVERITY_CRITICAL  = 2  # Action must be taken immediately. Indicates failure in a primary system.
SEVERITY_ERROR     = 3  # Indicates an error in secondary/redundant systems.
SEVERITY_WARNING   = 4  # Indicates about a possible future error if this is not resolved within a given timeframe. Example would be a low battery warning.
SEVERITY_NOTICE    = 5  # An unusual event has occurred, though not an error condition. This should be investigated for the root cause.
SEVERITY_INFO      = 6  # Normal operational messages. Useful for logging. No action is required for these messages.
SEVERITY_DEBUG     = 7  # Useful non-operational messages that can assist in debugging. These should not occur during normal operation.
SEVERITY_ALL_CLEAR = 10 # Unique to SOAR.  Indicates that an issue has been resolved (like comms restored).



class Logger():
	'''
	Allows publishing to a console-like topic, or simply printing to screen.
	topicName -- String.  Name of the ROS topic serving as the console.
	topicType -- Object.  An actual topic object.   FIXME -- Document/Explain!
	msgAttr   -- String.  Attribute of the topicType that is associated with the text message
	'''
	def __init__(self, topicName=None, topicType=None, msgAttr=None, queue_size=10):
		try:
			if (topicName and topicType):
				if (hasattr(topicType(), msgAttr)):
					import rospy
					self.consoleTopic = topicType
					self.consolePublisher = rospy.Publisher(topicName, topicType, queue_size=queue_size)
					self.msgAttr = msgAttr
				else:
					self.consolePublisher = None
					print('Logger:  Given msgAttr is not an attrib of given topicType.  Just using print()')
			else:
				self.consolePublisher = None
				print('Logger:  No topic name/type found.  Just using print()')
				
		except Exception as e:
			self.consolePublisher = None
			print(f'Error in Logger init: {e}')


	def log(self, msgtext, severity=SEVERITY_INFO, **kwargs):
		if (self.consolePublisher):
			# def pubConsole(pub, id, severity, msgtext, userID=0, speakMsg='', tune=px4Tunes.DEFAULT.value):
			try:
				c_msg          = self.consoleTopic()

				c_msg.__setattr__(self.msgAttr, msgtext)
	
				if (hasattr(c_msg, 'severity')):
					c_msg.severity = severity

				for key in c_msg.__slots__:
					if (key in kwargs):
						c_msg.__setattr__(key, kwargs[key])
								
				self.consolePublisher.publish(c_msg)
				
				if (len(kwargs) > 0):
					print(f'DEBUG FROM LOGGER: {msgtext}, {kwargs}')
				else:
					print(f'DEBUG FROM LOGGER: {msgtext}')
			except Exception as e:
				print(f"logger error: {e}.  Could not print {msgtext}")
		else:
			print(f'LOGGER: {msgtext}')	


# https://pyimagesearch.com/2018/12/17/image-stitching-with-opencv-and-python/


def _resolveTrackerFactory(name, cv2_module=cv2):
	'''
	Return the `Tracker<name>_create` factory callable from `cv2_module`,
	preferring `cv2_module.legacy.Tracker<name>_create` when that submodule
	exists and has it.

	OpenCV moved several classic trackers (Boosting, TLD, MedianFlow, MOSSE)
	into a `cv2.legacy` submodule starting around 4.5.1; CSRT/KCF/MIL stayed
	at the top level in both API shapes. Detecting capability directly
	(rather than guessing from `cv2.__version__`, which broke outright on
	OpenCV 5.x) is robust to any future OpenCV release that keeps either
	shape -- and raises a clear AttributeError, rather than silently
	picking the wrong shape, for one that has neither.

	`cv2_module` is injectable for testing (see tests/test_trackers.py) --
	real callers should never pass it.
	'''
	legacy = getattr(cv2_module, 'legacy', None)
	factory_name = f'Tracker{name}_create'
	if (legacy is not None) and hasattr(legacy, factory_name):
		return getattr(legacy, factory_name)
	return getattr(cv2_module, factory_name)


def _buildOpenCvObjectTrackers(cv2_module=cv2):
	'''Build the {name: factory} dict for OPENCV_OBJECT_TRACKERS. See _resolveTrackerFactory().'''
	return {
		key: _resolveTrackerFactory(name, cv2_module=cv2_module)
		for key, name in (
			('csrt', 'CSRT'),
			('kcf', 'KCF'),
			('boosting', 'Boosting'),
			('mil', 'MIL'),
			('tld', 'TLD'),
			('medianflow', 'MedianFlow'),
			('mosse', 'MOSSE'),
		)
	}


# A dictionary that maps strings to their corresponding OpenCV object
# tracker implementations. See _buildOpenCvObjectTrackers()/_resolveTrackerFactory().
OPENCV_OBJECT_TRACKERS = _buildOpenCvObjectTrackers()
		
# define names of each possible ArUco tag OpenCV supports
ARUCO_DICT = {
	"DICT_4X4_50":         {"dict": cv2.aruco.DICT_4X4_50,         "color": (244, 3, 252)},  # hot pink
	"DICT_4X4_100":        {"dict": cv2.aruco.DICT_4X4_100,        "color": (252, 3, 173)},  # purple
	"DICT_4X4_250":        {"dict": cv2.aruco.DICT_4X4_250,        "color": (252, 3, 98)},   # indigo
	"DICT_4X4_1000":       {"dict": cv2.aruco.DICT_4X4_1000,       "color": (143, 41, 1)},   # navy blue
	"DICT_5X5_50":         {"dict": cv2.aruco.DICT_5X5_50,         "color": (245, 140, 2)},  # bright blue
	"DICT_5X5_100":        {"dict": cv2.aruco.DICT_5X5_100,        "color": (245, 217, 2)},  # light blue
	"DICT_5X5_250":        {"dict": cv2.aruco.DICT_5X5_250,        "color": (196, 245, 2)},  # teal
	"DICT_5X5_1000":       {"dict": cv2.aruco.DICT_5X5_1000,       "color": (1, 138, 24)},   # dark green
	"DICT_6X6_50":         {"dict": cv2.aruco.DICT_6X6_50,         "color": (3, 252, 181)},  # lime green
	"DICT_6X6_100":        {"dict": cv2.aruco.DICT_6X6_100,        "color": (244, 3, 252)},  # hot pink
	"DICT_6X6_250":        {"dict": cv2.aruco.DICT_6X6_250,        "color": (252, 3, 173)},  # purple
	"DICT_6X6_1000":       {"dict": cv2.aruco.DICT_6X6_1000,       "color": (252, 3, 98)},   # indigo
	"DICT_7X7_50":         {"dict": cv2.aruco.DICT_7X7_50,         "color": (143, 41, 1)},   # navy blue
	"DICT_7X7_100":        {"dict": cv2.aruco.DICT_7X7_100,        "color": (245, 140, 2)},  # bright blue
	"DICT_7X7_250":        {"dict": cv2.aruco.DICT_7X7_250,        "color": (245, 217, 2)},  # light blue
	"DICT_7X7_1000":       {"dict": cv2.aruco.DICT_7X7_1000,       "color": (196, 245, 2)},  # teal
	"DICT_ARUCO_ORIGINAL": {"dict": cv2.aruco.DICT_ARUCO_ORIGINAL, "color": (1, 138, 24)},   # dark green
	"DICT_APRILTAG_16h5":  {"dict": cv2.aruco.DICT_APRILTAG_16h5,  "color": (3, 252, 181)},  # lime green
	"DICT_APRILTAG_25h9":  {"dict": cv2.aruco.DICT_APRILTAG_25h9,  "color": (2, 252, 252)},  # yellow
	"DICT_APRILTAG_36h10": {"dict": cv2.aruco.DICT_APRILTAG_36h10, "color": (3, 3, 252)}, 	 # red
	"DICT_APRILTAG_36h11": {"dict": cv2.aruco.DICT_APRILTAG_36h11, "color": (3, 186, 252)}   # orange
}


def _resolveArucoDictAndParams(dictID, cv2_module=cv2):
	'''
	Return (dict, params) for the given ARUCO_DICT[...]['dict'] value, via
	cv2_module.aruco.getPredefinedDictionary()/DetectorParameters().

	Requires OpenCV >=4.7 (the modern cv2.aruco dict/params API) -- both
	packages' pyproject.toml already declare opencv-contrib-python>=4.10.0,
	well past that boundary, and the deprecated Dictionary_get()/
	DetectorParameters_create() API this used to fall back to no longer
	exists at all on OpenCV 5.x. Raises AttributeError (via cv2_module.aruco)
	rather than silently limping along on the wrong API shape if that
	minimum isn't actually met.

	`cv2_module` is injectable for testing; real callers should never pass it.
	'''
	aruco = cv2_module.aruco
	return (aruco.getPredefinedDictionary(dictID), aruco.DetectorParameters())


ARUCO_DRAWING_DEFAULTS = {'borderDraw': True, 'borderColor': (3, 186, 252), 
						  'centerDraw': True, 'centerColor': (3, 186, 252), 'centerRadiusPx':   2, 
						  'arrowDraw':  True, 'arrowColor':  (3, 186, 252), 'arrowThicknessPx': 1, 'arrowLengthPx': 25, 'arrowTipLength': 0.3, 
						  'textDraw':   True, 'textColor':   (3, 186, 252), 'textThicknessPx':  1, 'textScale':     0.5}

# ==================================================================================
# From https://github.com/PyImageSearch/imutils/blob/master/imutils/paths.py

image_types = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")

def list_images(basePath, contains=None):
    # return the set of files that are valid
    return list_files(basePath, validExts=image_types, contains=contains)


def list_files(basePath, validExts=None, contains=None):
    # loop over the directory structure
    for (rootDir, dirNames, filenames) in os.walk(basePath):
        # loop over the filenames in the current directory
        for filename in filenames:
            # if the contains string is not none and the filename does not contain
            # the supplied string, then ignore the file
            if contains is not None and filename.find(contains) == -1:
                continue

            # determine the file extension of the current file
            ext = filename[filename.rfind("."):].lower()

            # check to see if the file is an image and should be processed
            if validExts is None or ext.endswith(validExts):
                # construct the path to the image and yield it
                imagePath = os.path.join(rootDir, filename)
                yield imagePath
# ==================================================================================
                

def cropImageHack(stitched):
	# create a 10 pixel border surrounding the stitched image
	print("Cropping image ...")
	stitched = cv2.copyMakeBorder(stitched, 10, 10, 10, 10,
		cv2.BORDER_CONSTANT, (0, 0, 0))
		
	# convert the stitched image to grayscale and threshold it
	# such that all pixels greater than zero are set to 255
	# (foreground) while all others remain 0 (background)
	gray = cv2.cvtColor(stitched, cv2.COLOR_BGR2GRAY)
	thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY)[1]
	
	# find all external contours in the threshold image then find
	# the *largest* contour which will be the contour/outline of
	# the stitched image
	cnts = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL,
		cv2.CHAIN_APPROX_SIMPLE)
	# cnts = imutils.grab_contours(cnts)
	cnts = cnts[0] if len(cnts) == 2 else cnts[1]
	c = max(cnts, key=cv2.contourArea)

	# allocate memory for the mask which will contain the
	# rectangular bounding box of the stitched image region
	mask = np.zeros(thresh.shape, dtype="uint8")
	(x, y, w, h) = cv2.boundingRect(c)
	cv2.rectangle(mask, (x, y), (x + w, y + h), 255, -1)		
	
	# create two copies of the mask: one to serve as our actual
	# minimum rectangular region and another to serve as a counter
	# for how many pixels need to be removed to form the minimum
	# rectangular region
	minRect = mask.copy()
	sub = mask.copy()
	# keep looping until there are no non-zero pixels left in the
	# subtracted image
	while cv2.countNonZero(sub) > 0:
		# erode the minimum rectangular mask and then subtract
		# the thresholded image from the minimum rectangular mask
		# so we can count if there are any non-zero pixels left
		minRect = cv2.erode(minRect, None)
		sub = cv2.subtract(minRect, thresh)		

	# find contours in the minimum rectangular mask and then
	# extract the bounding box (x, y)-coordinates
	cnts = cv2.findContours(minRect.copy(), cv2.RETR_EXTERNAL,
		cv2.CHAIN_APPROX_SIMPLE)
	# cnts = imutils.grab_contours(cnts)
	cnts = cnts[0] if len(cnts) == 2 else cnts[1]
	c = max(cnts, key=cv2.contourArea)
	(x, y, w, h) = cv2.boundingRect(c)

	# use the bounding box coordinates to extract our final
	# stitched image
	return stitched[y:y + h, x:x + w]
	
	

def stitchImages(imagesDirectory = None, imageFilenamesArray = None, doCrop = False, outputFile = None):
	'''
	Stitches a collection of input images,
	returning a single cv2 image.
	
	Inputs:
	imagesDirectory - A string containing the full path to a directory 
				containing the images you wish to stitch.  
				All image files in this directory will be included.
				Default: None
	imageFilenamsArray - A Python array of strings, where each 
				string is a full-path filename of an image file.
				Default: None
	doCrop - A boolean flag indicating whether the resulting stitched
			 image should be cropped.  See the pyimagesearch link 
			 for details.
			 Default: False
	outputFile - A string containing the full path (and filename) for 
			 the resulting stitched image.
			 Provide this if you want to save the image.
			 Default:  None (no file saved)
			
	You need to provide `imagesDirectory` or `imageFilenamesArray` 
	(or both, if you wish) 
	
	Returns status (0 if no errors) and the stitched image
	'''

	# grab the paths to the input images and initialize our images list
	print("Loading images ...")
	imagePaths = []
	if (imagesDirectory is not None):
		imagePaths = sorted(list(list_images(imagesDirectory)))
	if (imageFilenamesArray is not None):
		imagePaths.extend(imageFilenamesArray)
	if (len(imagePaths) == 0):
		print('Error: No images were found.')
		return (-1, None)

	images = []
	# loop over the image paths, load each one, and add them to our
	# images to stich list
	for imagePath in imagePaths:
		image = cv2.imread(imagePath)
		images.append(image)

	# initialize OpenCV's image sticher object and then perform the image
	# stitching
	print("Stitching images ...")
	stitcher = cv2.Stitcher_create()
	(status, stitched) = stitcher.stitch(images)
	
	# if the status is '0', then OpenCV successfully performed image stitching
	if status == 0:
		# check to see if we supposed to crop out the largest rectangular
		# region from the stitched image
		if (doCrop):
			stiched = cropImageHack(stitched)
		
		if (outputFile is not None):
			# write the output stitched image to disk
			cv2.imwrite(outputFile, stitched)

		# display the output stitched image to our screen
		cv2.imshow("Stitched", stitched)
		cv2.waitKey(0)

	else:
		# otherwise the stitching failed, 
		# likely due to not enough keypoints being detected
		print("Image stitching failed ({})".format(status))		
	
	return(status, stitched)
	

def arucoDrawDetections(img, corners, ids, centers=[], rotations=[], config=ARUCO_DRAWING_DEFAULTS):
	'''
	img is a numpy array of the cv2 image.
		We will update the image itself.
	corners - output from arucoDetectMarkers.  np INT array    FIXME -- Maybe it shouldn't be
	ids - output from arucoDetectMarkers.  np array of size n (number of detected markers)

	'''

	try:
		for i in range(0, len(corners)):
			# Draw bounding box:
			if (config['borderDraw']):
				cv2.polylines(img, corners[i].astype('int'), True, config['borderColor'], 2, cv2.LINE_AA)

			# Add label to bounding box (top right corner)
			if (config['textDraw']):
				cv2.putText(img, str(ids[i]),
					(int(corners[i][0][1][0]), int(corners[i][0][1][1]) - 7),
					cv2.FONT_HERSHEY_SIMPLEX,
					config['textScale'], config['textColor'], config['textThicknessPx'], cv2.LINE_AA)


		if ((config['centerDraw']) or (config['arrowDraw'])):
			for i in range(0, len(centers)):
				pt1 = (centers[i][0], centers[i][1])
				# Draw a center marker
				if (config['centerDraw']):
					drawCircle(img, pt1, config['centerRadiusPx'], color=config['centerColor'])
				if (config['arrowDraw']):
					# Draw an arrow marker
					pt2 = ptAndAngleToNewPt(centers[i], rotations[i], config['arrowLengthPx'])
					drawArrow(img, pt1, (int(pt2[0]), int(pt2[1])), config['arrowColor'], config['arrowThicknessPx'], config['arrowTipLength'])
	except Exception as e:
		print(f'ERROR in arucoDrawDetections:  {e}')


def arucoDetectMarkers(img, arucoDict, arucoParams, img_x_y=None, orig_x_y=None, detector=None):
	'''
	Detect ArUco markers in the input frame
	See https://pyimagesearch.com/2020/12/21/detecting-aruco-markers-with-opencv-and-python/

	img is a numpy array of the cv2 image
	arucoDict comes from cv2.aruco.getPredefinedDictionary()
	arucoParams comes from cv2.aruco.DetectorParameters()
	img_x_y is a tuple of form (width, height), describing size of img
	orig_x_y is also (width, height), describing size of original image (before scaling)
	detector -- an already-built cv2.aruco.ArucoDetector(arucoDict, arucoParams), for
		callers (like _Aruco) that construct/cache one once and reuse it across many
		calls. When None (the default), one is built fresh internally from arucoDict/
		arucoParams -- fine for one-shot callers like countArucoInImage(), since
		ArucoDetector construction is cheap relative to detectMarkers() itself.

	For example:
		self.arucoDict   = {'RPi':      cv2.aruco.getPredefinedDictionary(ARUCO_DICT['DICT_APRILTAG_16h5']),
							'HiRes':    cv2.aruco.getPredefinedDictionary(ARUCO_DICT['DICT_APRILTAG_16h5']),
							'Tracking': cv2.aruco.getPredefinedDictionary(ARUCO_DICT['DICT_APRILTAG_16h5'])}
		self.arucoParams = {'RPi':      cv2.aruco.DetectorParameters(),
							'HiRes':    cv2.aruco.DetectorParameters(),
							'Tracking': cv2.aruco.DetectorParameters()}

	See https://docs.opencv.org/4.x/d2/d1a/classcv_1_1aruco_1_1ArucoDetector.html#a0c1d14251bf1cbb06277f49cfe1c9b61
	NOTE (from above link): The function does not correct lens distortion or takes it into account. It's recommended to undistort input image with corresponding camera model, if camera parameters are known.

	centers -- n x 2 integer array denoting the [x, y] center coords for each of n identified markers.
	rotations -- n x 1 float array denoting each marker's rotation.  0 is up, pi/4 is right.
	corners -- n x 1 x 4 x 2 float array.
	'''
	try:
		centers   = np.array([], dtype='int')
		rotations = np.array([])
		if (detector is None):
			detector = cv2.aruco.ArucoDetector(arucoDict, arucoParams)
		(corners, ids, rejected) = detector.detectMarkers(img)
		if (len(corners) > 0):
			corners = np.array(corners)
			ids = ids.flatten()
			if (img_x_y != orig_x_y):
				# We changed image size.  Change corners to display properly when overlayed on original image
				xscale = orig_x_y[0] / img_x_y[0] 
				yscale = orig_x_y[1] / img_x_y[1]
				
				corners[:,:,:,0] *= xscale
				corners[:,:,:,1] *= yscale
			


			# Find midpoint, using corner points 1 (NE) and 3 (SW)
			'''
			for i in range(0, len(corners)):
				mp = ((corners[i][0][3][0] + corners[i][0][1][0])/2, 
					  (corners[i][0][3][1] + corners[i][0][1][1])/2) 
			'''
			centers = ((corners[:,0,3,:]+corners[:,0,1,:])/2).astype(int)

			'''
			for i in range(0, len(corners)):
				if (self.calcRotations):					
					# point 0 is top left, 3 is bottom left.  x increases to right, y increases down
					x = corners[i][0][0][0] - corners[i][0][3][0]
					y = corners[i][0][0][1] - corners[i][0][3][1]
					theta = math.atan2(x, -y)  # NOTE:  This is in [radians]
			'''
			rotations = np.arctan2( (corners[:,0,3,0]-corners[:,0,0,0]), -(corners[:,0,3,1]-corners[:,0,0,1]) )

			# corners = corners.astype(int)
			
	except Exception as e:
		print('ArUco Tracking failed: {}.'.format(str(e)))
		# (corners, ids, rejected, centers, rotations) = (np.array([], dtype='int'), None, None, np.array([], dtype='int'), np.array([]))
		(corners, ids, rejected, centers, rotations) = (np.array([]), None, None, np.array([], dtype='int'), np.array([]))

		# pubConsole(self.pub_console, self.assetID, MAV_SEVERITY_ERROR, 'ArUco Tracking failed ({}): {}.'.format(camType, str(e)))

	return (corners, ids, rejected, centers, rotations)


def decorateBarcode(img, corners, data, color=(0,0,255), addText=True):
	'''
	img is a numpy array of the cv2 image.
		We will update the image itself.
	corners - output from pyzbar.decode rect.  np INT32 array of arrays.
	data - output from pyzbar.decode data (text of barcode)
	'''
	try:
		for i in range(0, len(corners)):
			# Draw bounding box:
			# print('corners', i, ': ', corners[i])
			# cv2.polylines(img, [corners[i]], True, color, 2, cv2.LINE_AA)
			cv2.rectangle(img, corners[i][0], corners[i][1],
				color, 2, cv2.LINE_AA)

			
			'''
			FIXME -- This is giving an error about index 1..
			# Add label to bounding box (top right corner)
			if (addText):
				cv2.putText(img, str(data[i]),
					(corners[i][0][1][0], corners[i][0][1][1] - 7),
					cv2.FONT_HERSHEY_SIMPLEX,
					0.5, color, 1, cv2.LINE_AA)		
			'''
	except Exception as e:
		print(f'Error in decorateBarcode: {e}')


def decorateQR(img, corners, data, color=(0,0,255), addText=True):
	'''
	img is a numpy array of the cv2 image.
		We will update the image itself.
	corners -- list of 4x2 arrays (per detected QR code), in the decoder's own native
		corner order.
	data -- list of decoded payload strings, same length/order as corners.
	'''
	try:
		for i in range(0, len(corners)):
			pts = np.asarray(corners[i], dtype=np.int32).reshape((-1, 1, 2))
			cv2.polylines(img, [pts], True, color, 2, cv2.LINE_AA)

			if (addText):
				topPoint = corners[i][0]
				cv2.putText(img, str(data[i]),
					(int(topPoint[0]), int(topPoint[1]) - 7),
					cv2.FONT_HERSHEY_SIMPLEX,
					0.5, color, 1, cv2.LINE_AA)
	except Exception as e:
		print(f'Error in decorateQR: {e}')


_QR_ECC_MAP = {
	'L': qrcode.constants.ERROR_CORRECT_L,
	'M': qrcode.constants.ERROR_CORRECT_M,
	'Q': qrcode.constants.ERROR_CORRECT_Q,
	'H': qrcode.constants.ERROR_CORRECT_H,
}

# Lossless-only -- a lossy format (e.g. JPEG) can corrupt sharp module edges,
# undermining decodability independent of DPI. PNG is the recommended default.
_QR_OUTPUT_EXTENSIONS = {'.png', '.tif', '.tiff', '.bmp'}

# Fixed render box_size (px/module) before the final cv2.resize() to the
# exact target size -- generous enough to keep resize a downscale/mild
# upscale rather than a large magnification, which would blur module edges.
_QR_RENDER_BOX_SIZE = 10


def _normalizeColorToRGB(color):
	'''
	Accepts a PIL-recognized name/hex string (e.g. 'red', '#ff0000'), or an
	RGB 3-tuple/list of ints 0-255 (e.g. (255, 0, 0)) -- NOT a 4-tuple/RGBA.
	Deliberately does not just call PIL.ImageColor.getrgb() on every input:
	getrgb() only accepts strings and raises AttributeError on a tuple.
	'''
	if isinstance(color, str):
		return ImageColor.getrgb(color)
	if (isinstance(color, (tuple, list)) and len(color) == 3
			and all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in color)):
		return tuple(color)
	raise TypeError(f"color must be a name/hex string or an RGB 3-tuple of ints 0-255, got {color!r}")


def _qrRoundTripOk(img, payload):
	'''
	Thin, separately-named wrapper around cv2.QRCodeDetector so tests have a
	seam to force the "logo broke decoding" failure branch deterministically
	(via monkeypatch), instead of hunting for a payload/ECC/logo combination
	that happens to break real OpenCV decoding (version/build-dependent).

	Pads `img` with a temporary white margin before decoding -- NOT saved or
	returned, purely so cv2.QRCodeDetector isn't confused by the array
	having literally zero pixels beyond its own edge (an artifact of
	testing the bare in-memory array; empirically confirmed real
	printed/photographed tags with default border=0 decode fine once any
	ordinary surrounding whitespace, e.g. a printed page's own margins, is
	present -- this padding just reproduces that minimal condition for the
	in-memory check).
	'''
	pad = max(1, round(img.shape[0] * 0.15))
	canvas = np.full((img.shape[0] + 2 * pad, img.shape[1] + 2 * pad, 3), 255, dtype=np.uint8)
	canvas[pad:pad + img.shape[0], pad:pad + img.shape[1]] = img

	detector = cv2.QRCodeDetector()
	(data, points, _) = detector.detectAndDecode(canvas)
	return bool(data) and (points is not None) and (data == payload)


def generateQR(payload, tag_size_inches, dpi=300, ecc='H', border=0,
			   fill_color='black', back_color='white', label=None,
			   logo=None, logo_scale=0.2, outputFile=None):
	'''
	Generate a printable QR-code tag image, for use as a physical fiducial
	with olab_camera's QR detection/pose pipeline (_QRCode/findTagPose()).

	payload -- str, non-empty. The data to encode.
	tag_size_inches -- physical size of the QR symbol itself. With the
		default border=0, the saved image IS the QR symbol (no baked-in
		margin), so this is exactly what any ordinary print dialog's
		"actual size"/100% setting, or an image editor's "set image size",
		controls -- and exactly what a ruler measures on the printed page.
		Pass this same value into findTagPose()'s objectPoints for this
		printed tag, REGARDLESS of `border` (see `border` below -- a
		nonzero border only adds extra file size on top of this, it never
		changes what tag_size_inches itself means or the pose value to
		use). The QR symbol occupies round(tag_size_inches * dpi) pixels,
		within +/-1 pixel (exact-pixel sizing isn't achievable in general,
		since both module counts and final pixel dimensions must be
		integers).
	dpi -- int > 0. See tag_size_inches above, and outputFile below.
	ecc -- one of 'L'/'M'/'Q'/'H' (error-correction level). Default 'H'
		(~30% recoverable) for resilience of a printed tag viewed at an
		angle/distance/partial occlusion.
	border -- int >= 0 (extra quiet-zone width in modules baked INTO THE
		FILE itself, on top of tag_size_inches). Default 0: the saved
		image is exactly the QR symbol -- no baked-in margin -- so the
		file's own size always equals tag_size_inches exactly, matching
		what any print/sizing tool operates on. A QR code's quiet-zone
		requirement is about the physical scanning environment having
		clean white space around the printed symbol -- not about the file
		containing that space itself -- so with the default border=0,
		leave a reasonable plain white margin around the tag when you
		print/place it (an ordinary printed page's own margins are already
		more than enough for a single tag printed on its own sheet; don't
		crop tight to the black pixels or tile tags edge-to-edge with no
		gap). Pass a nonzero border only if you specifically want that
		margin baked into the file itself (e.g. to composite the tag into
		a larger design that doesn't otherwise leave it any white space)
		-- doing so makes the FILE larger than tag_size_inches (the QR
		symbol itself still prints at exactly tag_size_inches; the border
		is added on top, not carved out of it), so a nonzero border no
		longer gives you "whole file = what I asked for" -- you're
		explicitly trading that property away for baked-in margin.
	fill_color / back_color -- a name/hex string (e.g. 'red', '#ff0000') or
		an RGB 3-tuple/list of ints 0-255 (e.g. (255, 0, 0)) -- not RGBA.
	label -- optional str drawn below the QR symbol, on extra canvas
		appended outside tag_size_inches (does not alter or shrink the QR
		symbol itself).
	logo -- optional path (str/os.PathLike) or numpy.ndarray (grayscale,
		BGR, or BGRA, dtype uint8) composited centered over the QR, inside
		an opaque back_color backing square. logo_scale is a *side-length*
		fraction of the QR symbol bounding that backing square (the actual
		occlusion footprint, margin included) -- area coverage is
		logo_scale ** 2, capped at 0.5 (25% area). After compositing, the
		result is verified to still decode to `payload`; raises
		RuntimeError if the logo broke decodability.
	outputFile -- optional path (str/os.PathLike). If given, the image is
		also saved via Pillow with `dpi` embedded as real file metadata
		(the returned numpy array itself carries no DPI -- only outputFile
		gets correct physical-print sizing). Extension must be one of
		.png/.tif/.tiff/.bmp (lossless only -- PNG recommended). Print
		outputFile at actual size / 100% scale (no "fit to page"/"scale to
		fit"), and measure the printed tag with a ruler before trusting it
		for pose.

	Returns a BGR uint8 numpy array.
	'''
	# ---- Validation (before any rendering or file I/O) ----
	if not isinstance(payload, str):
		raise TypeError(f"payload must be a str, got {type(payload).__name__}")
	if payload == '':
		raise ValueError("payload must not be empty")

	if not (isinstance(tag_size_inches, (int, float)) and not isinstance(tag_size_inches, bool)):
		raise TypeError(f"tag_size_inches must be an int or float, got {type(tag_size_inches).__name__}")
	if not math.isfinite(tag_size_inches):
		raise ValueError(f"tag_size_inches must be finite, got {tag_size_inches}")
	if tag_size_inches <= 0:
		raise ValueError(f"tag_size_inches must be > 0, got {tag_size_inches}")

	if not (isinstance(dpi, int) and not isinstance(dpi, bool)):
		raise TypeError(f"dpi must be an int, got {type(dpi).__name__}")
	if dpi <= 0:
		raise ValueError(f"dpi must be > 0, got {dpi}")

	if not isinstance(ecc, str):
		raise TypeError(f"ecc must be a str, got {type(ecc).__name__}")
	if ecc not in _QR_ECC_MAP:
		raise ValueError(f"ecc must be one of {sorted(_QR_ECC_MAP)}, got {ecc!r}")

	if not (isinstance(border, int) and not isinstance(border, bool)):
		raise TypeError(f"border must be an int, got {type(border).__name__}")
	if border < 0:
		raise ValueError(f"border must be >= 0, got {border}")

	fill_rgb = _normalizeColorToRGB(fill_color)
	back_rgb = _normalizeColorToRGB(back_color)

	if (label is not None) and not isinstance(label, str):
		raise TypeError(f"label must be a str or None, got {type(label).__name__}")

	logoImg = None
	if logo is not None:
		if not (isinstance(logo_scale, (int, float)) and not isinstance(logo_scale, bool)):
			raise TypeError(f"logo_scale must be an int or float, got {type(logo_scale).__name__}")
		if not math.isfinite(logo_scale):
			raise ValueError(f"logo_scale must be finite, got {logo_scale}")
		if not (0 < logo_scale <= 0.5):
			raise ValueError(f"logo_scale must be > 0 and <= 0.5 (25% area), got {logo_scale}")

		if isinstance(logo, (str, os.PathLike)):
			logoImg = cv2.imread(str(logo), cv2.IMREAD_UNCHANGED)
			if logoImg is None:
				raise ValueError(f"could not read logo image from {logo!r}")
		elif isinstance(logo, np.ndarray):
			logoImg = logo
			if logoImg.dtype != np.uint8:
				raise TypeError(f"logo array must have dtype uint8, got {logoImg.dtype}")
			if 0 in logoImg.shape:
				raise ValueError(f"logo array must not have a zero-length dimension, got shape {logoImg.shape}")
			if logoImg.ndim not in (2, 3):
				raise ValueError(f"logo array must be 2-D (grayscale) or 3-D (BGR/BGRA), got ndim={logoImg.ndim}")
			if (logoImg.ndim == 3) and (logoImg.shape[2] not in (3, 4)):
				raise ValueError(f"logo array's last dimension must be 3 (BGR) or 4 (BGRA), got {logoImg.shape[2]}")
		else:
			raise TypeError(f"logo must be a str/os.PathLike path or a numpy.ndarray, got {type(logo).__name__}")

		if logoImg.ndim == 2:
			logoImg = cv2.cvtColor(logoImg, cv2.COLOR_GRAY2BGR)

	if outputFile is not None:
		if not isinstance(outputFile, (str, os.PathLike)):
			raise TypeError(f"outputFile must be a str or os.PathLike, got {type(outputFile).__name__}")
		ext = os.path.splitext(str(outputFile))[1].lower()
		if ext not in _QR_OUTPUT_EXTENSIONS:
			raise ValueError(f"outputFile extension {ext!r} not supported; use one of {sorted(_QR_OUTPUT_EXTENSIONS)}")

	# ---- Build the QR matrix, then render at a fixed box_size and resize
	# the whole canvas to an exact pixel target (see plan/reviewer notes:
	# an integer box_size alone can't hit an exact target -- module_count *
	# box_size can differ from dpi * tag_size_inches by up to half a module) ----
	qr = qrcode.QRCode(error_correction=_QR_ECC_MAP[ecc], border=border, box_size=_QR_RENDER_BOX_SIZE)
	qr.add_data(payload)
	qr.make(fit=True)
	module_count = qr.modules_count

	target_symbol_px = round(dpi * tag_size_inches)
	if target_symbol_px < module_count:
		raise ValueError(
			f"tag_size_inches={tag_size_inches} at dpi={dpi} yields {target_symbol_px}px, "
			f"too small to render {module_count} modules (need >= 1px/module)")

	pilImg = qr.make_image(fill_color=fill_rgb, back_color=back_rgb).get_image().convert('RGB')
	img = cv2.cvtColor(np.array(pilImg), cv2.COLOR_RGB2BGR)

	full_modules = module_count + 2 * border
	target_full_px = round(target_symbol_px * full_modules / module_count)
	img = cv2.resize(img, (target_full_px, target_full_px), interpolation=cv2.INTER_NEAREST)

	# ---- Optional logo compositing ----
	if logo is not None:
		back_bgr = back_rgb[::-1]

		# logo_scale bounds the backing square (the true opaque occlusion
		# footprint, margin included), not just the logo's own pixels.
		backing_square_px = round(logo_scale * target_symbol_px)
		margin_px = round(0.08 * backing_square_px)
		inner_px = max(1, backing_square_px - 2 * margin_px)

		(logoH, logoW) = logoImg.shape[:2]
		scale = inner_px / max(logoH, logoW)
		newW = max(1, round(logoW * scale))
		newH = max(1, round(logoH * scale))
		resizedLogo = cv2.resize(logoImg, (newW, newH), interpolation=cv2.INTER_AREA)

		(cy, cx) = (target_full_px // 2, target_full_px // 2)
		half = backing_square_px // 2
		(bx0, by0) = (cx - half, cy - half)
		(bx1, by1) = (bx0 + backing_square_px, by0 + backing_square_px)
		cv2.rectangle(img, (bx0, by0), (bx1, by1), back_bgr, thickness=-1)

		(lx0, ly0) = (cx - newW // 2, cy - newH // 2)
		(lx1, ly1) = (lx0 + newW, ly0 + newH)

		if resizedLogo.shape[2] == 4:
			alpha = resizedLogo[:, :, 3:4].astype(np.float32) / 255.0
			logoBgr = resizedLogo[:, :, :3].astype(np.float32)
			roi = img[ly0:ly1, lx0:lx1].astype(np.float32)
			img[ly0:ly1, lx0:lx1] = (alpha * logoBgr + (1 - alpha) * roi).astype(np.uint8)
		else:
			img[ly0:ly1, lx0:lx1] = resizedLogo

		if not _qrRoundTripOk(img, payload):
			raise RuntimeError(
				f"logo overlay broke QR decodability for payload {payload!r} "
				f"(logo_scale={logo_scale}, ecc={ecc}); try a smaller logo_scale or a higher ecc")

	# ---- Optional label (extra canvas below the QR square -- not part of
	# tag_size_inches or the quiet zone) ----
	if label is not None:
		labelHeight = max(20, round(0.15 * target_full_px))
		labelCanvas = np.full((labelHeight, target_full_px, 3), back_rgb[::-1], dtype=np.uint8)

		thickness = max(1, round(labelHeight / 20))
		fontScale = 0.5
		while True:
			((textW, textH), _baseline) = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fontScale + 0.1, thickness)
			if (textW > 0.9 * target_full_px) or (textH > 0.6 * labelHeight):
				break
			fontScale += 0.1
		((textW, textH), _baseline) = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fontScale, thickness)
		textPos = (max(0, (target_full_px - textW) // 2), (labelHeight + textH) // 2)
		drawText(labelCanvas, label, textPos, fontScale=fontScale, thickness=thickness, color=fill_rgb[::-1])

		img = np.vstack([img, labelCanvas])

	# ---- Optional file output (DPI embedded, lossless formats only) ----
	if outputFile is not None:
		rgbOut = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
		Image.fromarray(rgbOut).save(str(outputFile), dpi=(dpi, dpi))

	return img


def decorateCalibrate(img, checkerboard, corners, count, img_x_y, orig_x_y, addText=True):
	try:
		if ((checkerboard is not None) and (corners is not None)):			
			# corners comes from a deque.  Below, we modify the values.  So, let's make a copy.
			cnrs = corners.copy()
			
			if (img_x_y != orig_x_y):
				# We changed image size.  Change corners to display properly when overlayed on original image
				xscale = orig_x_y[0] / img_x_y[0] 
				yscale = orig_x_y[1] / img_x_y[1]

				cnrs[:,:,0] *= xscale
				cnrs[:,:,1] *= yscale

			cv2.drawChessboardCorners(img, checkerboard, cnrs, True)	

		if (addText):
			cv2.putText(img, str(count),
				(15, 65),
				cv2.FONT_HERSHEY_SIMPLEX,
				0.5, (200, 20, 10), 1, cv2.LINE_AA)		

	except Exception as e:
		print(f'Error in decorateCalibrate: {e}')

def decorateFaceDetect(img, confidence, corners, color=(0, 255, 255), addText=True):
	'''
	img is a numpy array of the cv2 image.
		We will update the image itself.
	confidence - array of confidence values
	corners - output from olab_utils.detectFaces(): a list of
		[(x1,y1),(x2,y2)] int-pixel-coordinate pairs per face (top-left,
		bottom-right).
	'''
	try:
		for i in range(0, len(corners)):
			# Draw bounding box:
			# print('corners', i, ': ', corners[i])
			# cv2.polylines(img, [corners[i]], True, color, 2, cv2.LINE_AA)
			cv2.rectangle(img, corners[i][0], corners[i][1],
				color, 2, cv2.LINE_AA)


			'''
			FIXME -- This is giving an error about index 1..
			# Add label to bounding box (top right corner)
			if (addText):
				cv2.putText(img, str(data[i]),
					(corners[i][0][1][0], corners[i][0][1][1] - 7),
					cv2.FONT_HERSHEY_SIMPLEX,
					0.5, color, 1, cv2.LINE_AA)
			'''
	except Exception as e:
		print(f'Error in decorateFaceDetect: {e}')


def _resolveFaceDetector(modelFile, input_size, score_threshold, backend_id, target_id, cv2_module=cv2):
	'''
	Build a cv2.FaceDetectorYN (YuNet) for the given model file.

	modelFile -- full path to a YuNet ONNX model file (e.g.
		".../cv2_dnn_models/face_detection_yunet_2023mar.onnx").
	input_size -- (width, height) of the image detect() will be called with.
		Required at construction time by cv2.FaceDetectorYN.create() -- there
		is no way to create a detector without it, unlike the deprecated
		Caffe/TensorFlow DNN API this replaces.
	score_threshold -- passed through as create()'s score_threshold.
	backend_id, target_id -- passed through as create()'s backend_id/target_id
		(e.g. cv2.dnn.DNN_BACKEND_DEFAULT/DNN_TARGET_CPU for CPU inference,
		cv2.dnn.DNN_BACKEND_CUDA/DNN_TARGET_CUDA for GPU).

	`cv2_module` is injectable for testing; real callers should never pass it.
	'''
	return cv2_module.FaceDetectorYN.create(
		modelFile, '', input_size,
		score_threshold=score_threshold,
		backend_id=backend_id, target_id=target_id)


def detectFaces(img, detector, img_x_y=None, orig_x_y=None):
	'''
	Run face detection on `img` using an already-built cv2.FaceDetectorYN
	(see _resolveFaceDetector()), returning (confidence, corners, landmarks).

	detector.detect(img) returns (retval, faces), where faces is either None
	(no detections) or an [num_faces, 15] array per face:
		[x, y, w, h, x_re, y_re, x_le, y_le, x_nt, y_nt, x_rmc, y_rmc, x_lmc, y_lmc, score]
	(bbox top-left + width/height, right eye, left eye, nose tip, right mouth
	corner, left mouth corner, score).

	confidence -- list of scores, one per detected face.
	corners -- list of [(x1,y1),(x2,y2)] int-pixel-coordinate pairs per face
		(top-left, bottom-right) -- same shape decorateFaceDetect() expects,
		and the same contract the old SSD-based detector published (ints,
		regardless of any resizing).
	landmarks -- list of 5 (x,y) int-pixel-coordinate tuples per face (right
		eye, left eye, nose tip, right mouth corner, left mouth corner).

	img_x_y -- (width, height) of `img` (the frame actually passed to
		detect()), if it differs from the original capture resolution.
	orig_x_y -- (width, height) of the original capture resolution.
	When img_x_y != orig_x_y, every (x,y) point (bbox corners and landmarks
	alike) is scaled from img_x_y back to orig_x_y -- same xscale/yscale
	approach as arucoDetectMarkers() -- before being rounded to int. YuNet
	always returns floats regardless of whether any scaling is needed, so
	the int(round(...)) conversion happens unconditionally.
	'''
	(ret, faces) = detector.detect(img)
	if (faces is None):
		return ([], [], [])

	if (img_x_y is not None) and (orig_x_y is not None) and (img_x_y != orig_x_y):
		xscale = orig_x_y[0] / img_x_y[0]
		yscale = orig_x_y[1] / img_x_y[1]
	else:
		xscale = 1.0
		yscale = 1.0

	def _scalePoint(x, y):
		return (int(round(x * xscale)), int(round(y * yscale)))

	confidence = []
	corners    = []
	landmarks  = []
	for face in faces:
		(x, y, w, h) = face[0:4]
		confidence.append(face[14])
		corners.append([_scalePoint(x, y), _scalePoint(x + w, y + h)])
		landmarks.append([
			_scalePoint(face[4],  face[5]),   # right eye
			_scalePoint(face[6],  face[7]),   # left eye
			_scalePoint(face[8],  face[9]),   # nose tip
			_scalePoint(face[10], face[11]),  # right mouth corner
			_scalePoint(face[12], face[13]),  # left mouth corner
		])

	return (confidence, corners, landmarks)


def decorateOptFlow(img, shift):
	'''
	shift[0] x direction
	shift[1] y direction

	'''
	shp = img.shape  # [rows, cols, depth]
	[center_x, center_y] = [int(shp[1]/2), int(shp[0]/2)]
	drawCircle(img, (center_x, center_y), int(5*math.sqrt(shift[0]*shift[0] + shift[1]*shift[1])))
	# drawCircle(img, (center_x, center_y), 20)
	
	drawLine(img, (center_x, center_y), (int(center_x+5*shift[0]), int(center_y+5*shift[1])))
	
def decorateUltralytics(img, w, h, idName, results, drawBox, drawLabel, maskOutline):
	try:
		# FIXME -- assign color based on class
		color=(0, 255, 55)
		
		if (drawBox or drawLabel):
			for i in range(0, len(results['class'])):
				# 'class': [], 'class_conf': [],
				# print(results['xyxy'])
				if (results['xyxy']):
					pt1 = (int(results['xyxy'][i][0]), int(results['xyxy'][i][1]))
					pt2 = (int(results['xyxy'][i][2]), int(results['xyxy'][i][3]))

					if (drawBox):
						cv2.rectangle(img, pt1, pt2, color, 2, cv2.LINE_AA)
					
					if (drawLabel):
						txt = f"{results['class'][i]} {results['class_conf'][i]:.2f}"
						if (results['id']):
							txt = f"ID:{int(results['id'][i])} {txt}" 	
						txtsize = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 1)[0]  # scale=0.7, thickness=1
						shp = img.shape  # [rows, cols, depth]
						if (pt1[0] > shp[1]/2):
							# right justify across bounding box
							txt_x = pt2[0] - txtsize[0] - 5 # 5px buffer
							label_pt1 = (min(pt1[0], pt2[0] - txtsize[0] - 5), max(0, pt1[1]-20))
							label_pt2 = (pt2[0], label_pt1[1] + 20)
						else:
							# left justify
							txt_x = pt1[0] + 5
							label_pt1 = (pt1[0], max(0, pt1[1]-20))
							label_pt2 = (max(pt2[0], pt1[0]+txtsize[0]+5), label_pt1[1] + 20)

						cv2.rectangle(img, label_pt1, label_pt2, color, -1)

						cv2.putText(img, txt,
							(txt_x, label_pt2[1]-3),
							cv2.FONT_HERSHEY_SIMPLEX,
							0.7, (0, 0, 0), 1, cv2.LINE_AA)		
				elif (results['xyxyxyxy']):
					# print(np.array(results['xyxyxyxy'][i]).reshape((4,2)))
					cv2.polylines(img, [np.array(results['xyxyxyxy'][i]).reshape((4,2))], isClosed=True, color=(0, 255, 0), thickness=1)
		
		
		# min_conf = 0.75
		radius = 5 # px
		color = (100, 100, 100)
		thickness = 1
		skeleton = {'face':  [5, 3, 1, 0, 2, 4, 6], # [[0, 1], [0, 2], [1, 3], [2, 4], [3, 5], [4, 6]], 
		            'arms':  [9, 7, 5, 6, 8, 10], 
					'leg_left':  [11, 13, 15],
					'leg_right': [12, 14, 16],
					'torso': [5, 11, 12, 6]}
		sk_poly_colors = {'face': (50, 168, 82), 
					 'arms': (0, 154, 196), 
					 'leg_left': (250, 187, 0), 
					 'leg_right': (250, 187, 0), 
					 'torso': (240, 129, 231)}
		sk_points = {'face': [0, 1, 2, 3, 4], 
					 'arms': [5, 6, 7, 8, 9, 10],
					 'legs': [11, 12, 13, 14, 15, 16]}
		sk_points_colors = {'face': sk_poly_colors['face'], 
							'arms': sk_poly_colors['arms'], 
							'legs': sk_poly_colors['leg_left']}
					 
		
		
		# FIXME -- Need to scale the keypoints to image size?  Done?
		for body in range(0, len(results['keypoints'])):
			#print('sk')
			for part in skeleton:
				keep = []
				tmp = []
				for index in skeleton[part]:
					#print(f'{body=}, {index=}')
					#print(f"{results['keypoints'][body]}")
					if ((results['keypoints'][body][index] > [0, 0]).all()):
						tmp.append(results['keypoints'][body][index])
					else:
						if (len(tmp) > 1):
							keep.extend(tmp)
						tmp = []
						
				if (len(tmp) > 1):
					keep.extend(tmp)
				if (len(keep) > 1):
					cv2.polylines(img, [np.int32(keep)], isClosed=False, color=sk_poly_colors[part], thickness=2)
												
			#print('kp')
			for part in sk_points:
				for i in sk_points[part]:
					# print(results['keypoints'][body][i], results['keypoints'][body][i] > [0, 0])
					if ((results['keypoints'][body][i] > [0, 0]).all()):
						cv2.circle(img, results['keypoints'][body][i], radius, sk_points_colors[part], -1)
		
		# Segmentation
		# mask outline
		if (maskOutline):
			for i in range(0, len(results['masks_xy'])):
				cv2.polylines(img, [np.int32(results['masks_xy'][i])], isClosed=True, color=(100, 100, 100), thickness=2)
		
		# channel = 1
		# value = 40
		clr = np.array([100, 100, 100])

		# t = results[0].orig_img
		# t[:,:,channel] = t[:,:,channel]  + mask_stretch*value # mask_stretch*value # t[:,:,channel]#  + mask_stretch*value
		# t[:,:,:] = t[:,:,:]  + np.expand_dims(mask_stretch, axis=-1)*clr # mask_stretch*value # t[:,:,channel]#  + mask_stretch*value
		# success = cv2.imwrite('test00a.jpg', t)	
		try:	
			for i in range(0, len(results['masks_data'])):
				# print(results['masks_data'][i].shape)
				# print(type(results['masks_data'][i]))
				# print(results['masks_data'][i].dtype)
				# print(img.shape)
				# print(max(results['masks_data'][i]))  failsThe truth value of an array with more than one element is ambiguous. Use a.any() or a.all()
				img[:,:,:] = img[:,:,:] + np.expand_dims(results['masks_data'][i], axis=-1)*clr
		except Exception as e:
			print(f'ERRROR: {e}')
			
			
		'''
		if (addText):
			cv2.putText(img, str(idName),
				(15, 65),
				cv2.FONT_HERSHEY_SIMPLEX,
				0.5, (200, 20, 10), 1, cv2.LINE_AA)		
		'''
	except Exception as e:
		print(f'Error in decorateUltralytics: {e}')
		
def degCtoF(degC):
	# Convert degrees Celsius to Fahrenheit
	return (degC*9/5) + 32

def degFtoC(degF):
	# Convert degrees Fahrenheit to Celsius
	return (degF - 32) * 5/9
	
def drawArrow(img, pt1, pt2, color=(255,0,0), thickness=1, tipLength=0.1):
	try:
		cv2.arrowedLine(img, pt1, pt2, color, thickness, cv2.LINE_AA, 0, tipLength) 
	except Exception as e:
		print(f'ERROR in drawArrow: {e}')
		
def drawCircle(img, center, radius, thickness=3, color=(150, 25, 25)):
	'''
	cv2.circle(img, center, radius, color, thickness=1, lineType=8, shift=0)

	img (CvArr) – Image where the circle is drawn
	center (CvPoint) – Center of the circle
	radius (int) – Radius of the circle
	color (CvScalar) – Circle color
	thickness (int) – Thickness of the circle outline if positive, 
		otherwise this indicates that a filled circle is to be drawn
	lineType (int) – Type of the circle boundary, see Line description
		8 (or omitted) - 8-connected line.
		4 - 4-connected line.
		CV_AA - antialiased line.
	shift (int) – Number of fractional bits in the center coordinates and radius value	
	'''
	cv2.circle(img, center, radius, color, thickness, cv2.LINE_AA, 0)
	
def drawLine(img, p1, p2, thickness=3, color=(255,0,0)):
	cv2.line(img, p1, p2, color, thickness, cv2.LINE_AA)

def drawText(img, text, position, fontScale=0.7, thickness=2, color=(255, 255, 255), font=cv2.FONT_HERSHEY_SIMPLEX):
	'''
	cv2.putText(img, text, org, fontFace, fontScale, color, thickness, lineType)

	img (CvArr) – Image where the text is drawn
	text (str) – Text string to be drawn
	position (tuple) – Bottom-left corner of the text string in the image (x, y)
	fontScale (float) – Font scale factor
	thickness (int) – Thickness of the lines used to draw the text
	color (CvScalar) – Text color
	font (int) – Font type (e.g., cv2.FONT_HERSHEY_SIMPLEX)
	'''
	cv2.putText(img, text, position, font, fontScale, color, thickness, cv2.LINE_AA)


def res2rowscols(res):
	'''
	Split a screen resolution of form `widthxheight` into a list of 
	2 integers: [rows, cols]
	'''
	[cols, rows] = res.split('x')
	return [int(rows.strip()), int(cols.strip())]
	
	
def roiDrawBox(img, box, color=(255,255,255)):
	# check to see if the tracking was a success
	(x, y, w, h) = [int(v) for v in box]
	cv2.rectangle(img, (x, y), (x + w, y + h),
		color, 2, cv2.LINE_AA)

	# initialize the set of information we'll be displaying on
	# the frame
	# (H, W) = output['RPi'].myNumpyArray.shape[:2]
	'''
	cv2.putText(output['RPi'].myNumpyArray, 'tracking', (10, 25),
		cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)
	'''		

def roiTrack(roiTracker, img):
	'''
	Identify ROI
	
	This should only be called if ROI is actually active
	(i.e., if the bounding box has been defined, self.roiBB is not None)
	'''
	try:
		# grab the new bounding box coordinates of the object
		# (success, box) = self.roiTracker[camType].update(output[camType].myNumpyArray)
		(success, box) = roiTracker.update(img)			

	except Exception as e:
		print('ROI Tracking failed: {}.'.format(str(e)))
		# pubConsole(self.pub_console, self.assetID, MAV_SEVERITY_ERROR, 'ROI Tracking failed on {}: {}.'.format(camType, str(e)))
		# output[camType].setCamFunction(None)    # FIXME -- Need to update status to indicate we're no longer tracking ROI
		success = False
		box = None
		
	return (success, box)


def countArucoInImage(img, arucoDict, arucoParams, drawDetections=False, labelDetections=False):
	'''
	Count how many ArUco markers are in an image, grouped by marker ID.
	Returns a dictionary, where the keys are detected marker IDs, and the values are 
	the number of observations of the key ID.
	'''
	
	(corners, ids, rejected, centers, rotations) = arucoDetectMarkers(img, arucoDict, arucoParams)
	
	print(ids)
	IDcount = {}
	
	for i in range(0, len(corners)):
		markerID = ids[i]
		if (markerID in IDcount):
			IDcount[markerID] += 1
		else:
			IDcount[markerID]  = 1

	if (drawDetections):
		arucoDrawDetections(img, corners, ids, centers=centers, rotations=rotations)
			
	return IDcount
	


def map_range(x, X_min, X_max, Y_min, Y_max):
	''' 
	Linear mapping between two ranges of values 
	'''

	X_range = X_max - X_min
	Y_range = Y_max - Y_min
	XY_ratio = X_range / Y_range

	# y = ((x-X_min) / XY_ratio + Y_min) //1
	y = ((x-X_min) / XY_ratio + Y_min)

	return y
	
def setEndingChar(string, endingChar):
	'''
	Make sure `string` ends in `endingChar`, without allowing duplicates.
	'''
	string = string.rstrip()
	if (string[-1] != endingChar):
		string += endingChar
	return string
	
def _passFunction(*args, **kwargs):
	'''
	a dummy function that does nothing
	'''
	pass

def ptAndAngleToNewPt(pt, angleRad, length):
	'''
	Find the location of a new point that is `length` units from `pt`, in the direction `angleRad`
	pt -- (x, y)
	NOTE:  y increases DOWN
	'''
	return (pt[0] + length*math.sin(angleRad), pt[1] - length*math.cos(angleRad))


		
		
def arucoFindTagIndices(idArray, targetID):
	'''
	Given a 1-D np array of IDs and a specific reference ID, 
	find the indices of the array that match the reference.
	If no matches are found, return an empty tuple.
	'''
	try:
		if (idArray is None):
			return ()
		
		ids, = np.where(idArray.flatten() == targetID)
		return ids  # This will be a tuple
		
	except Exception as e:
		# raise Exception(e)
		return ()

def arucoFindTagIndicesList(idArray, targetIDlist):
	'''
	Given a 1-D np array of IDs and a python list of reference IDs, 
	find the indices of the array that match the reference.
	If no matches are found, return an empty tuple.
	'''
	try:
		if (idArray is None):
			return ()
		
		ids, = np.where(np.isin(idArray.flatten(), targetIDlist))
		return ids  # This will be a tuple
		
	except Exception as e:
		# raise Exception(e)
		return ()
	

"""	
NO!  arucoDetectMarkers already returns `centers`
def arucoFindTagCenterPixels(corners):
	'''
	Given the corners **for a single tag**, 
	return the (x, y) pixel coordinates of the tag's center.
	x is pixels from left of image; y is pixels from top of image.
	
	NOTE:  I'm pretty sure the x,y values are floats
	'''
	
	try:
		# Find midpoint, using corner points 1 (NE) and 3 (SW)
		mp = ((corners[0][3][0] + corners[0][1][0])/2, 
			  (corners[0][3][1] + corners[0][1][1])/2) 		
		return mp	  
	except Exception as e:
		# raise exception(e)?
		return ()
"""	

def findTagPose(objPoints, corners, cameraMatrix, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE):
	'''
	markerLength = 0.45  # [meters], but you can choose any unit you wish
	objPoints = np.array([[-markerLength/2,  markerLength/2, 0],
						  [ markerLength/2,  markerLength/2, 0],
						  [ markerLength/2, -markerLength/2, 0],
						  [-markerLength/2, -markerLength/2, 0]])

	corners **for a single tag**
	cameraMatrix is 3x3, containing fx, fy, cx, and cy
	dist is the array of distortion coefficients

	Not ArUco-specific despite the historical name of its deprecated alias
	(arucoFindPose()) -- works for any single planar tag's 4 corners, ArUco
	or QR.
	'''
	(ret, rvecs, tvecs) = cv2.solvePnP(objPoints, corners, cameraMatrix, dist, flags)

	return (ret, rvecs, tvecs)


def arucoFindPose(*args, **kwargs):
	'''Deprecated alias for findTagPose() -- see issue #21. Kept working indefinitely.'''
	warnings.warn(
		"olab_utils.arucoFindPose() is deprecated; use findTagPose() instead "
		"(not ArUco-specific -- see issue #21).",
		DeprecationWarning, stacklevel=2)
	return findTagPose(*args, **kwargs)


# self.camera.intrinsics['matrix'], self.camera.intrinsics['dist']

# Fixed rotation from ROS camera-link convention (x forward, y left, z up --
# an FLU frame, same handedness as the vehicle body frame) to OpenCV's
# optical convention (x right, y down, z forward), as used by cv2.solvePnP()/
# findTagPose(). This is a constant fact about the two conventions
# (REP-103 / image_geometry), not a per-robot calibration value:
#   optical_x = -link_y, optical_y = -link_z, optical_z = link_x
_R_OPTICAL_FROM_CAMERALINK = np.array([
	[0.0, -1.0,  0.0],
	[0.0,  0.0, -1.0],
	[1.0,  0.0,  0.0],
])
_R_CAMERALINK_FROM_OPTICAL = _R_OPTICAL_FROM_CAMERALINK.T


def _rpyToMatrix(roll, pitch, yaw):
	'''
	ZYX (yaw-pitch-roll) intrinsic Tait-Bryan angles -> rotation matrix,
	mapping vectors expressed in a body-style frame (FLU: x forward, y left,
	z up) into the parent frame -- the convention used by
	findTagPoseGlobal()/findCameraPoseGlobal() (ROS REP-103, ENU
	world frame).
	'''
	cr, sr = math.cos(roll),  math.sin(roll)
	cp, sp = math.cos(pitch), math.sin(pitch)
	cy, sy = math.cos(yaw),   math.sin(yaw)
	Rx = np.array([[1.0, 0.0, 0.0], [0.0, cr, -sr], [0.0, sr, cr]])
	Ry = np.array([[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]])
	Rz = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]])
	return Rz @ Ry @ Rx


def _matrixToRpy(R):
	'''
	Inverse of _rpyToMatrix(). At pitch = +/-90 degrees (gimbal lock), roll
	and yaw are not individually recoverable from R -- only their sum or
	difference is -- so this fixes roll=0 and folds the whole rotation into
	yaw. The specific (roll, yaw) split returned there may not match whatever
	values originally produced R, but _rpyToMatrix(*_matrixToRpy(R)) always
	reconstructs the same rotation matrix R, which is what
	findTagPoseGlobal()/findCameraPoseGlobal() (which only ever
	compose via rotation matrices internally, converting to/from RPY at
	their input/output boundary) actually rely on.
	'''
	sp = max(-1.0, min(1.0, -R[2, 0]))

	# |cos(pitch)|, computed directly from matrix entries (R[2,1] = cp*sin(roll),
	# R[2,2] = cp*cos(roll), so hypot(R[2,1], R[2,2]) = cp exactly, given an
	# exact R) rather than via cos(asin(sp)). This matters because sp is
	# *quadratically* insensitive to the actual pitch deviation from the pole
	# (sin(pi/2 - eps) = 1 - eps^2/2 + ...): thresholding on `1 - abs(sp)`
	# directly (an earlier version of this function did) silently treated a
	# real, valid attitude ~0.06 degrees from vertical as gimbal-locked and
	# discarded its nonzero roll -- a genuine attitude error, not gimbal
	# ambiguity. `cp` computed this way is linear in the deviation, so a
	# small, numerically-justified threshold on it does not have that problem.
	cp = math.hypot(R[2, 1], R[2, 2])
	pitch = math.atan2(sp, cp)

	# `cp` here is a magnitude derived from matrix entries that are each
	# accurate to within a few ULPs of double precision, even after R has
	# been composed through a handful of rotation-matrix multiplications (as
	# in findCameraPoseGlobal()) -- so a threshold a few orders of
	# magnitude above machine epsilon (but many orders below any physically
	# meaningful attitude, like the ~1.7e-2 rad/~1 degree used in this
	# module's tests) safely separates "exactly singular, perturbed only by
	# floating-point roundoff" from "genuinely near, but not at, the pole".
	if (cp > 1e-8):
		roll = math.atan2(R[2, 1], R[2, 2])
		yaw  = math.atan2(R[1, 0], R[0, 0])
	else:
		# Gimbal lock: fix roll = 0 and recover the remaining combined
		# roll+yaw (or roll-yaw) rotation entirely as yaw.
		roll = 0.0
		if (sp > 0):   # pitch = +90 deg
			yaw = math.atan2(R[1, 2], R[1, 1])
		else:          # pitch = -90 deg
			yaw = math.atan2(-R[0, 1], R[1, 1])

	return (roll, pitch, yaw)


def _identityPose():
	return {'position': (0.0, 0.0, 0.0), 'orientation': (0.0, 0.0, 0.0)}


def findTagPoseGlobal(cameraPose, rvec, tvec, cameraExtrinsics=None):
	'''
	Estimate a detected tag's pose in the world frame, given the camera's own
	world-frame pose and the tag's pose relative to the camera (rvec/tvec,
	from findTagPose()/cv2.solvePnP() -- OpenCV optical frame: x right, y
	down, z forward).

	Not ArUco-specific -- works for any single planar tag's rvec/tvec,
	including QR (see olab_camera.addQR()).

	cameraPose -- {'position': (x, y, z), 'orientation': (roll, pitch, yaw)}.
		The vehicle body's pose in the world (ENU) frame: position in meters,
		orientation in radians, FLU body convention (x forward, y left, z
		up), per REP-103. This is typically camera.pose (see
		olab_camera.Camera.setPose()) -- e.g. from flight-controller state,
		not the camera's own pose.
	rvec, tvec -- the tag's pose relative to the camera, in OpenCV's optical
		frame, as returned by findTagPose()/cv2.solvePnP().
	cameraExtrinsics -- {'position': (x, y, z), 'orientation': (roll, pitch, yaw)},
		the camera's fixed mount pose relative to the vehicle body frame (same
		units/convention as cameraPose). Typically camera.extrinsics (see
		olab_camera.Camera.setExtrinsics()). None (default) is treated as
		identity (camera at the body origin, boresight aligned with the
		body's +x/forward axis).

	Returns (position, orientation): position is (x, y, z) meters in world
	(ENU). orientation is (roll, pitch, yaw) radians, describing the tag's
	own local frame (z = tag normal, pointing out of its printed face) in
	the same body-style RPY convention as cameraPose.
	'''
	if (cameraExtrinsics is None):
		cameraExtrinsics = _identityPose()

	R_wb = _rpyToMatrix(*cameraPose['orientation'])
	t_wb = np.array(cameraPose['position'], dtype=np.float64)
	R_bc = _rpyToMatrix(*cameraExtrinsics['orientation'])
	t_bc = np.array(cameraExtrinsics['position'], dtype=np.float64)
	R_co = _R_CAMERALINK_FROM_OPTICAL

	R_ot, _ = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64))
	t_ot = np.asarray(tvec, dtype=np.float64).reshape(3)

	R_wt = R_wb @ R_bc @ R_co @ R_ot
	t_wt = t_wb + R_wb @ t_bc + R_wb @ R_bc @ R_co @ t_ot

	return (tuple(t_wt.tolist()), _matrixToRpy(R_wt))


def arucoFindPoseGlobal(*args, **kwargs):
	'''Deprecated alias for findTagPoseGlobal() -- see issue #21. Kept working indefinitely.'''
	warnings.warn(
		"olab_utils.arucoFindPoseGlobal() is deprecated; use findTagPoseGlobal() instead "
		"(not ArUco-specific -- see issue #21).",
		DeprecationWarning, stacklevel=2)
	return findTagPoseGlobal(*args, **kwargs)


def findCameraPoseGlobal(tagPose, rvec, tvec, cameraExtrinsics=None):
	'''
	Inverse of findTagPoseGlobal(): given a tag's *known* world-frame pose
	and its pose relative to the camera in the current detection, estimate
	the vehicle's world-frame pose (e.g. for precision landing).

	tagPose -- {'position': (x, y, z), 'orientation': (roll, pitch, yaw)}, the
		tag's known pose in the world (ENU) frame, in the same convention as
		findTagPoseGlobal()'s returned orientation (the tag's orientation
		expressed as if it were a body frame).
	rvec, tvec -- the tag's pose relative to the camera (OpenCV optical
		frame), from the current detection.
	cameraExtrinsics -- see findTagPoseGlobal(); None defaults to identity.

	Returns (position, orientation): the vehicle body's world pose -- the
	same meaning as cameraPose in findTagPoseGlobal() (not the camera's
	own pose; cameraExtrinsics and the fixed optical/body-frame conversion
	are factored out automatically, since a precision-landing caller wants
	vehicle pose, not camera-mount pose).
	'''
	if (cameraExtrinsics is None):
		cameraExtrinsics = _identityPose()

	R_bc = _rpyToMatrix(*cameraExtrinsics['orientation'])
	t_bc = np.array(cameraExtrinsics['position'], dtype=np.float64)
	R_co = _R_CAMERALINK_FROM_OPTICAL

	R_ot, _ = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64))
	t_ot = np.asarray(tvec, dtype=np.float64).reshape(3)

	R_wt = _rpyToMatrix(*tagPose['orientation'])
	t_wt = np.array(tagPose['position'], dtype=np.float64)

	R_chain = R_bc @ R_co @ R_ot   # body_T_optical_via_mount, rotation only
	R_wb = R_wt @ R_chain.T
	t_wb = t_wt - R_wb @ (t_bc + R_bc @ R_co @ t_ot)

	return (tuple(t_wb.tolist()), _matrixToRpy(R_wb))


def arucoFindCameraPoseGlobal(*args, **kwargs):
	'''Deprecated alias for findCameraPoseGlobal() -- see issue #21. Kept working indefinitely.'''
	warnings.warn(
		"olab_utils.arucoFindCameraPoseGlobal() is deprecated; use findCameraPoseGlobal() instead "
		"(not ArUco-specific -- see issue #21).",
		DeprecationWarning, stacklevel=2)
	return findCameraPoseGlobal(*args, **kwargs)


def findTagPoses(corners_list, ids_or_data, tag_size, cameraMatrix, dist,
				  cameraPose=None, cameraExtrinsics=None, flags=cv2.SOLVEPNP_IPPE_SQUARE):
	'''
	Reusable postFunction boilerplate for ArUco/QR pose: for each detection
	(corners_list[i] paired with ids_or_data[i]), builds objPoints from
	tag_size and computes local pose via findTagPose(), and -- when
	cameraPose is given -- also the world-frame pose via findTagPoseGlobal().
	Works identically for camera.aruco[idName].deque[0] (ids_or_data =
	['ids']) and camera.qr[idName].deque[0] (ids_or_data = ['data']), since
	both expose the same corners shape. See docs/usage_guide.md's ArUco/QR
	sections for full worked examples.

	corners_list -- per-detection corner arrays, e.g.
		camera.aruco[idName].deque[0]['corners'] or
		camera.qr[idName].deque[0]['corners'].
	ids_or_data -- parallel list identifying each detection: ArUco numeric
		ids or QR payload strings. Must be the same length as corners_list.
	tag_size -- physical size of the tag's printed square, in **meters**
		(use inches2meters() if you have inches), applied to every
		detection in this call. Must be a finite, positive real number
		(not a bool).
	cameraMatrix, dist -- camera intrinsics, e.g.
		camera.intrinsics[res]['matrix']/['dist'].
	cameraPose, cameraExtrinsics -- optional; see findTagPoseGlobal(). When
		cameraPose is omitted, worldPosition/worldOrientation are None.
	flags -- passed through to findTagPose()/cv2.solvePnP().

	Returns a list of dicts, one per detection whose solvePnP call
	succeeded (a failed detection -- ret=False -- is silently omitted, the
	same way today's hand-rolled `if (ret):` examples just skip printing on
	failure):
		{'id': ..., 'rvec': ..., 'tvec': ..., 'distance': ...,
		 'worldPosition': ..., 'worldOrientation': ...}
	'tvec' carries the raw per-axis (x, y, z) camera-frame offsets in
	meters. 'distance' is the Euclidean norm of tvec (straight-line range,
	not just z-depth). 'worldPosition'/'worldOrientation' are None unless
	cameraPose is given.

	Raises ValueError (before any solvePnP call) if len(corners_list) !=
	len(ids_or_data), or if tag_size is not a finite, positive real numeric
	scalar (bool is explicitly rejected, since bool is an int subclass in
	Python and would otherwise silently coerce True/False to 1/0).
	'''
	if (len(corners_list) != len(ids_or_data)):
		raise ValueError(
			f'corners_list and ids_or_data must be the same length, '
			f'got {len(corners_list)} and {len(ids_or_data)}.')

	if isinstance(tag_size, bool) or not isinstance(tag_size, (int, float, np.integer, np.floating)):
		raise ValueError(f'tag_size must be a real numeric scalar (int or float), got {type(tag_size)!r}.')
	if (not math.isfinite(tag_size)) or (tag_size <= 0):
		raise ValueError(f'tag_size must be finite and positive, got {tag_size!r}.')

	ml = tag_size
	objPoints = np.array([[-ml/2,  ml/2, 0],
						  [ ml/2,  ml/2, 0],
						  [ ml/2, -ml/2, 0],
						  [-ml/2, -ml/2, 0]])

	results = []
	for i in range(len(corners_list)):
		(ret, rvec, tvec) = findTagPose(objPoints, corners_list[i], cameraMatrix, dist, flags=flags)
		if not ret:
			continue

		tvecFlat = np.asarray(tvec, dtype=np.float64).reshape(3)
		distance = float(np.linalg.norm(tvecFlat))

		worldPosition = None
		worldOrientation = None
		if (cameraPose is not None):
			(worldPosition, worldOrientation) = findTagPoseGlobal(cameraPose, rvec, tvec, cameraExtrinsics)

		results.append({
			'id': ids_or_data[i],
			'rvec': rvec,
			'tvec': tvec,
			'distance': distance,
			'worldPosition': worldPosition,
			'worldOrientation': worldOrientation,
		})

	return results


def checkPort(port):
	# Check if a given local port is available (returns True) or in use (returns False)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('localhost', port))
        s.close()
        return True
    except socket.error as e:
        if e.errno == errno.EADDRINUSE:
            print(f"Port {port} is already in use.")
        else:
            print(e)
        s.close()
        return False
        
def getIP():
	"""Return the machine's primary outbound IP address.

	Uses a UDP socket to query the OS routing table without sending any data,
	which reliably identifies the IP address of the network interface that would
	be used for outbound traffic. Falls back to '127.0.0.1' if the lookup fails
	(e.g., no network interfaces are available).

	Returns:
		str: The local IP address (e.g., '192.168.1.42'), or '127.0.0.1' on failure.

	Notes:
		- Works on Windows, macOS, and Linux.
		- Does not send any network traffic.

	Example:
		>>> import olab_utils
		>>> olab_utils.getIP()
		'192.168.1.42'
	"""
	s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	try:
		s.connect(('10.255.255.255', 1))
		return s.getsockname()[0]
	except Exception:
		return '127.0.0.1'
	finally:
		s.close()

def findOpenPort(port, options=range(8000,8011)):
	# port is our preferred port
	# options is a list of acceptable alternative ports
	if (checkPort(port)):
		return port
	else:
		for p in options:
			if (checkPort(p)):
				return p
				
	return None	
		
def pics2video(sourcePath=None, filename=None, fps=30):
	try:
		
		if sourcePath is None:
			print('Error in pic2video - no sourcePath defined')
			return
			
		sourcePath = setEndingChar(sourcePath, "/")
				
				
		if filename is None:
			myTimestamp = datetime.today()
			filename = f"{myTimestamp.strftime('%Y-%m-%d-%H%M%S')}.mp4"


		# Create a list of image files, ordered by timestamp:
		# See https://stackoverflow.com/questions/30121222/convert-all-images-in-directory-to-mp4-using-ffmpeg-and-a-timestamp-order
		# ls /path/to/*.jpg | sort -V | xargs -I {} echo "file '{}'" > list.txt
		os.system(f"ls {sourcePath}*.jpg | sort -V | xargs -I {{}} echo \"file '{{}}'\" > {sourcePath}list.txt")

		# Create video:
		# ffmpeg -r 1/5 -f concat -i list.txt -c:v libx264 -r 25 -pix_fmt yuv420p -t 15 out.mp4
		# ffmpeg -r 1/5 -f concat -safe 0 -i list.txt -c:v libx264 -r 30 -pix_fmt yuv420p -vf scale=540:-2 -t 15 out.mp4 
		os.system(f"ffmpeg -r {fps} -f concat -safe 0 -i {sourcePath}list.txt -c:v libx264 -pix_fmt yuv420p -y {sourcePath}{filename}")

		# Twitter doesn't like the format created above.
		# We'll post-process with ffmpeg:
		# os.system("ffmpeg -i {}/{} -vcodec libx264 -f mp4 {}/{} -y".format(dirpath, tmpFilename, dirpath, filename))
		# os.system("rm {}/{}".format(dirpath, tmpFilename))
	except Exception as e:
		print(f'Error in pics2video: {e}')


def inches2meters(inches):
    return inches * 0.0254

def meters2inches(meters):
    return meters * 39.3701
