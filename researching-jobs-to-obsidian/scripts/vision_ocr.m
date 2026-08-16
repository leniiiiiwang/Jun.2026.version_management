#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>

static NSDictionary *FailureResult(NSString *path, NSString *category) {
    return @{
        @"path": path ?: @"",
        @"ok": @NO,
        @"text": @"",
        @"observations": @[],
        @"error": category,
    };
}

static NSDictionary *BoxDictionary(CGRect box) {
    return @{
        @"x": @(box.origin.x),
        @"y": @(box.origin.y),
        @"width": @(box.size.width),
        @"height": @(box.size.height),
    };
}

static NSComparisonResult CompareObservations(NSDictionary *left, NSDictionary *right) {
    CGRect leftBox = CGRectMake([left[@"_x"] doubleValue], [left[@"_y"] doubleValue],
                                [left[@"_width"] doubleValue], [left[@"_height"] doubleValue]);
    CGRect rightBox = CGRectMake([right[@"_x"] doubleValue], [right[@"_y"] doubleValue],
                                 [right[@"_width"] doubleValue], [right[@"_height"] doubleValue]);
    CGFloat leftTop = CGRectGetMaxY(leftBox);
    CGFloat rightTop = CGRectGetMaxY(rightBox);
    if (leftTop > rightTop) return NSOrderedAscending;
    if (leftTop < rightTop) return NSOrderedDescending;
    if (leftBox.origin.x < rightBox.origin.x) return NSOrderedAscending;
    if (leftBox.origin.x > rightBox.origin.x) return NSOrderedDescending;
    return NSOrderedSame;
}

static NSDictionary *RecognizeImage(NSString *path, NSArray<NSString *> *languages) {
    NSImage *image = [[NSImage alloc] initWithContentsOfFile:path];
    if (image == nil) return FailureResult(path, @"image_load_failed");

    NSRect proposed = NSMakeRect(0, 0, image.size.width, image.size.height);
    CGImageRef cgImage = [image CGImageForProposedRect:&proposed context:nil hints:nil];
    if (cgImage == NULL) return FailureResult(path, @"image_load_failed");

    VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
    request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
    request.usesLanguageCorrection = YES;
    request.recognitionLanguages = languages;
    VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:cgImage options:@{}];
    NSError *recognitionError = nil;
    if (![handler performRequests:@[request] error:&recognitionError]) {
        return FailureResult(path, @"recognition_failed");
    }

    NSMutableArray<NSDictionary *> *recognized = [NSMutableArray array];
    for (VNRecognizedTextObservation *observation in request.results) {
        VNRecognizedText *candidate = [[observation topCandidates:1] firstObject];
        if (candidate == nil || candidate.string.length == 0) continue;
        CGRect box = observation.boundingBox;
        [recognized addObject:@{
            @"text": candidate.string,
            @"confidence": @(candidate.confidence),
            @"bounding_box": BoxDictionary(box),
            @"_x": @(box.origin.x), @"_y": @(box.origin.y),
            @"_width": @(box.size.width), @"_height": @(box.size.height),
        }];
    }
    [recognized sortUsingComparator:^NSComparisonResult(NSDictionary *left, NSDictionary *right) {
        return CompareObservations(left, right);
    }];

    NSMutableArray<NSDictionary *> *observations = [NSMutableArray arrayWithCapacity:recognized.count];
    NSMutableArray<NSString *> *lines = [NSMutableArray arrayWithCapacity:recognized.count];
    for (NSDictionary *record in recognized) {
        [observations addObject:@{
            @"text": record[@"text"],
            @"confidence": record[@"confidence"],
            @"bounding_box": record[@"bounding_box"],
        }];
        [lines addObject:record[@"text"]];
    }
    return @{
        @"path": path,
        @"ok": @YES,
        @"text": [lines componentsJoinedByString:@"\n"],
        @"observations": observations,
        @"error": [NSNull null],
    };
}

static BOOL PrintJSON(NSDictionary *result) {
    if (![NSJSONSerialization isValidJSONObject:result]) return NO;
    NSError *jsonError = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:result options:0 error:&jsonError];
    if (data == nil) return NO;
    NSFileHandle *output = [NSFileHandle fileHandleWithStandardOutput];
    [output writeData:data];
    [output writeData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
    return YES;
}

static NSArray<NSString *> *LanguagesFromArgument(NSString *argument) {
    NSMutableArray<NSString *> *languages = [NSMutableArray array];
    for (NSString *value in [argument componentsSeparatedByString:@","]) {
        NSString *trimmed = [value stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        if (trimmed.length == 0) return nil;
        [languages addObject:trimmed];
    }
    return languages.count == 0 ? nil : languages;
}

static void PrintUsage(void) {
    fprintf(stderr, "usage: vision_ocr [--languages zh-Hans,en-US] IMAGE...\n");
}

static int SelfTest(NSArray<NSString *> *languages) {
    NSFileManager *manager = [NSFileManager defaultManager];
    NSURL *directory = [[manager temporaryDirectory] URLByAppendingPathComponent:[[NSUUID UUID] UUIDString] isDirectory:YES];
    NSURL *imageURL = [directory URLByAppendingPathComponent:@"vision-ocr-self-test.png"];
    NSError *fileError = nil;
    BOOL created = [manager createDirectoryAtURL:directory withIntermediateDirectories:NO attributes:nil error:&fileError];
    if (!created) return 1;

    NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc]
        initWithBitmapDataPlanes:NULL pixelsWide:1200 pixelsHigh:240 bitsPerSample:8 samplesPerPixel:4
        hasAlpha:YES isPlanar:NO colorSpaceName:NSCalibratedRGBColorSpace bitmapFormat:0 bytesPerRow:0 bitsPerPixel:0];
    BOOL wrote = NO;
    if (bitmap != nil) {
        NSGraphicsContext *context = [NSGraphicsContext graphicsContextWithBitmapImageRep:bitmap];
        [NSGraphicsContext saveGraphicsState];
        [NSGraphicsContext setCurrentContext:context];
        [[NSColor whiteColor] setFill];
        NSRectFill(NSMakeRect(0, 0, 1200, 240));
        NSDictionary *attributes = @{
            NSFontAttributeName: [NSFont boldSystemFontOfSize:84],
            NSForegroundColorAttributeName: [NSColor blackColor],
        };
        [@"INTERVIEW 2026" drawAtPoint:NSMakePoint(48, 76) withAttributes:attributes];
        [NSGraphicsContext restoreGraphicsState];
        wrote = [[bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToURL:imageURL options:NSDataWritingAtomic error:&fileError];
    }

    NSDictionary *result = wrote ? RecognizeImage(imageURL.path, languages) : FailureResult(imageURL.path, @"self_test_image_failed");
    BOOL printed = PrintJSON(result);
    NSString *text = [result[@"text"] isKindOfClass:[NSString class]] ? result[@"text"] : @"";
    BOOL passed = [result[@"ok"] boolValue] && [text rangeOfString:@"INTERVIEW" options:NSCaseInsensitiveSearch].location != NSNotFound;
    NSError *cleanupError = nil;
    BOOL removed = [manager removeItemAtURL:directory error:&cleanupError];
    return printed && passed && removed ? 0 : 1;
}

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        NSArray<NSString *> *arguments = [[NSProcessInfo processInfo] arguments];
        NSArray<NSString *> *languages = @[@"zh-Hans", @"en-US"];
        NSUInteger index = 1;
        if (arguments.count > 1 && [arguments[1] isEqualToString:@"--languages"]) {
            if (arguments.count < 3) {
                PrintUsage();
                return 64;
            }
            languages = LanguagesFromArgument(arguments[2]);
            if (languages == nil) {
                PrintUsage();
                return 64;
            }
            index = 3;
        }
        if (arguments.count > index && [arguments[index] isEqualToString:@"--self-test"]) {
            return arguments.count == index + 1 ? SelfTest(languages) : (PrintUsage(), 64);
        }
        if (arguments.count <= index) {
            PrintUsage();
            return 64;
        }

        int exitCode = 0;
        for (; index < arguments.count; index++) {
            NSDictionary *result = RecognizeImage(arguments[index], languages);
            if (!PrintJSON(result) || ![result[@"ok"] boolValue]) exitCode = 1;
        }
        return exitCode;
    }
}
